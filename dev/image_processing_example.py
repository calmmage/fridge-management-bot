
import asyncio
import base64
import json
from datetime import datetime
from textwrap import dedent
from typing import Optional

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import ( InlineKeyboardButton,
                           InlineKeyboardMarkup, Message, ReplyKeyboardRemove)
# from app.app import App
from botspot import commands_menu
from botspot.components.qol.bot_commands_menu import Visibility
from botspot.user_interactions import (ask_user_choice, ask_user_choice_raw,
                                       ask_user_raw)
from botspot.utils import send_safe
from botspot.utils.admin_filter import AdminFilter
from litellm import acompletion
from loguru import logger
from pydantic import BaseModel

# from app.app import App, GraduateType, TargetCity
from app.router import commands_menu

# Create router
router = Router()
# app = App()


# Check if it's an early registration (before March 15)
EARLY_REGISTRATION_DATE = datetime.strptime("2025-03-15", "%Y-%m-%d")
EARLY_REGISTRATION_DATE_HUMAN = "15 Марта"


async def process_payment(
    message: Message,
    state: FSMContext,
    city: str,
    graduation_year: int,
    skip_instructions=False,
    graduate_type: str = GraduateType.GRADUATE.value,
):
    """Process payment for an event registration"""
    # Check if we have original user information in the state
    # This happens when the function is called from a callback handler
    state_data = await state.get_data()
    user_id = state_data.get("original_user_id")
    username = state_data.get("original_username", "")
    
    # Ensure user_id is an integer
    if user_id is not None:
        user_id = int(user_id)
    else:
        # Use message.from_user.id as fallback
        user_id = message.from_user.id if message.from_user else None
        
    # Ensure username is a string
    if username is None:
        username = message.from_user.username or "" if message.from_user else ""
        
    logger.info(f"Using original user information: ID={user_id}, username={username}")

    # Get user registration to get graduate_type
    if user_id:
        registration = await app.get_user_registration(user_id)
        if registration and "graduate_type" in registration:
            graduate_type = registration["graduate_type"]

    # Calculate payment amount
    regular_amount, discount, discounted_amount, formula_amount = app.calculate_payment_amount(
        city, graduation_year, graduate_type
    )

    # Only show instructions if not skipped
    if not skip_instructions:
        from botspot.core.dependency_manager import get_dependency_manager

        deps = get_dependency_manager()
        await deps.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        await asyncio.sleep(3)  # 3 second delay

        # Prepare payment message - split into parts for better UX
        if city == TargetCity.MOSCOW.value:
            payment_formula = "1000р + 200 * (2025 - год выпуска)"
        elif city == TargetCity.PERM.value:
            payment_formula = "500р + 100 * (2025 - год выпуска)"
        else:  # Saint Petersburg
            payment_formula = "за свой счет"

        # only display formula if not a friend of school
        if graduate_type != GraduateType.NON_GRADUATE.value:
            payment_msg_part1 = dedent(
                f"""
                💰 Оплата мероприятия
                
                Для оплаты мероприятия используется следующая формула:
                
                {city} → {payment_formula}
            """
            )

            # Send part 1
            await send_safe(message.chat.id, payment_msg_part1)

            # Delay between messages
            await asyncio.sleep(5)

        # Check if we're before the early registration deadline
        today = datetime.now()
        is_early_registration_period = today < EARLY_REGISTRATION_DATE

        formula_message = ""
        if formula_amount > regular_amount:
            formula_message = f"Рекомендованный взнос по формуле: {formula_amount} руб."

        if is_early_registration_period:
            payment_msg_part2 = dedent(
                f"""
                Для вас минимальный взнос: {regular_amount} руб. {formula_message}
                
                При ранней оплате (до {EARLY_REGISTRATION_DATE_HUMAN}) - скидка. 
                Минимальный взнос при ранней оплате - {discounted_amount} руб.
                
                Но если перевести больше, то на мероприятие сможет прийти еще один первокурсник 😊
                """
            )
        else:
            payment_msg_part2 = dedent(
                f"""
                Для вас минимальный взнос: {regular_amount} руб.
                {formula_message}
                
                Но если перевести больше, то на мероприятие сможет прийти еще один первокурсник 😊
                """
            )

        # Send part 2
        await send_safe(message.chat.id, payment_msg_part2)

        # Delay between messages
        await asyncio.sleep(3)

        # Prepare part 3 with payment details
        payment_msg_part3 = dedent(
            f"""
            Реквизиты для оплаты:
            В Тинькофф банк по номеру телефона
            Номер телефона - {app.settings.payment_phone_number}
            Получатель - {app.settings.payment_name}
            """
        )

        # Send part 3
        await send_safe(message.chat.id, payment_msg_part3)

        # Delay between messages
        await asyncio.sleep(3)

    # Create choices for the user
    choices = {
        "pay_later": "Оплачу позже",
        "too_expensive": "Ой, нет, что-то слишком дорого, я передумал",
    }

    # Wait for response using ask_user_choice_raw (either screenshot or choice)
    # Log payment proof request
    await app.save_event_log(
        "payment_action", 
        {
            "action": "request_payment_proof",
            "city": city,
            "amount": discounted_amount,
            "regular_amount": regular_amount,
            "graduate_type": graduate_type
        }, 
        user_id, 
        username
    )
    
    response = await ask_user_choice_raw(
        message.chat.id,
        "Пожалуйста, отправьте скриншот подтверждения оплаты (фото или PDF) или выберите опцию ниже:",
        choices=choices,
        state=state,
        timeout=3600,
    )

    if response is None:
        # No response received
        await send_safe(
            message.chat.id,
            "⏰ Не получен ответ в течение 20 минут. Пожалуйста, используйте команду /pay для оплаты.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Check if response is a string (meaning it's a choice selection)
    if isinstance(response, str):
        if response == "pay_later":
            # User clicked "Pay Later"
            await send_safe(
                message.chat.id,
                "Хорошо! Вы можете оплатить позже, используя команду /pay",
                reply_markup=ReplyKeyboardRemove(),
            )

            # Log to chat log
            await app.log_registration_step(
                user_id=user_id, username=username, step="Нажал 'Оплачу позже'"
            )
            
            # Log to event logs
            await app.save_event_log(
                "payment_action", 
                {
                    "action": "pay_later_selected",
                    "city": city,
                    "amount": discounted_amount,
                    "regular_amount": regular_amount,
                    "graduate_type": graduate_type
                }, 
                user_id, 
                username
            )

            # Save payment info with pending status
            await app.save_payment_info(
                user_id, city, discounted_amount, regular_amount, formula_amount=formula_amount
            )
            return False
        elif response == "too_expensive":
            # User clicked "Too expensive, changed my mind"
            # Log to chat log
            assert user_id is not None, "User ID cannot be None for payment cancellation"
            
            await app.log_registration_step(
                user_id=user_id, username=username, step="Отказ от оплаты: слишком дорого"
            )
            
            # Log to event logs
            await app.save_event_log(
                "payment_action", 
                {
                    "action": "too_expensive_selected",
                    "city": city,
                    "amount": discounted_amount,
                    "regular_amount": regular_amount,
                    "graduate_type": graduate_type
                }, 
                user_id, 
                username
            )
            
            # Get all user registrations
            registrations = await app.get_user_registrations(user_id)
            # Find the registration for this city
            registration = next((reg for reg in registrations if reg["target_city"] == city), None)
            
            if registration:
                full_name = registration.get("full_name", "Unknown")
                # Delete the registration for this city
                await app.delete_user_registration(user_id, city)
                
                # Log cancellation
                await app.log_registration_canceled(
                    user_id,
                    username,
                    full_name,
                    city,
                )
                
                await send_safe(
                    message.chat.id,
                    "Понимаем! Ваша регистрация отменена. Если передумаете, вы всегда можете зарегистрироваться снова с помощью команды /start",
                    reply_markup=ReplyKeyboardRemove(),
                )
            else:
                await send_safe(
                    message.chat.id,
                    "Что-то пошло не так. Пожалуйста, используйте команду /cancel_registration для отмены регистрации.",
                    reply_markup=ReplyKeyboardRemove(),
                )
            
            return False

    # Otherwise, it's a message with photo or document
    # Check if response has photo or document (PDF)
    has_photo = hasattr(response, "photo") and response.photo
    has_pdf = (
        hasattr(response, "document")
        and response.document
        and response.document.mime_type == "application/pdf"
    )

    if has_photo or has_pdf:
        # Log payment proof submission
        await app.save_event_log(
            "payment_action", 
            {
                "action": "payment_proof_submitted",
                "city": city,
                "amount": discounted_amount,
                "proof_type": "photo" if has_photo else "pdf",
                "graduate_type": graduate_type
            }, 
            user_id, 
            username
        )
        
        # Save payment info with pending status
        await app.save_payment_info(
            user_id,
            city,
            discounted_amount,
            regular_amount,
            response.message_id,
            formula_amount=formula_amount,
            username=username,
        )

        # Forward screenshot to events chat (which is used as validation chat)
        try:
            # Get events chat ID from settings
            events_chat_id = app.settings.events_chat_id

            # if today is before early registration -> "discounted_amount (later {regular amount}}" else "regular_amount"

            today = datetime.now()
            if today < EARLY_REGISTRATION_DATE:
                needs_to_pay = f"{discounted_amount} руб (после {EARLY_REGISTRATION_DATE_HUMAN} - {regular_amount} руб)"
            else:
                needs_to_pay = f"{regular_amount} руб"

            # Get user info for the message
            user_info = f"👤 Пользователь: {username or ''} (ID: {user_id})\n"
            user_info += f"📍 Город: {city}\n"
            user_info += f"💰 Сумма к оплате: {needs_to_pay}\n"

            # Get user registration for additional info
            user_registration = await app.get_user_registration(user_id)
            if user_registration:
                user_info += f"👤 ФИО: {user_registration.get('full_name', 'Неизвестно')}\n"

                # Add graduate type info
                graduate_type = user_registration.get("graduate_type", GraduateType.GRADUATE.value)
                if graduate_type == GraduateType.TEACHER.value:
                    user_info += f"👨‍🏫 Статус: Учитель (бесплатно)\n"
                elif graduate_type == GraduateType.NON_GRADUATE.value:
                    user_info += f"👥 Статус: Друг школы (не выпускник)\n"
                else:
                    user_info += f"🎓 Выпуск: {user_registration.get('graduation_year', 'Неизвестно')} {user_registration.get('class_letter', '')}\n"

            # Get bot instance
            from botspot.core.dependency_manager import get_dependency_manager

            deps = get_dependency_manager()
            bot = deps.bot

            # Try to parse payment amount from the screenshot/PDF

            payment_info = await parse_payment_info(response, has_photo, has_pdf, deps.bot)

            # Create validation buttons
            validation_buttons = []

            # If we successfully parsed a valid amount, show simplified buttons
            if payment_info.is_valid:
                # Add parsed amount button
                validation_buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"✅ {payment_info.amount} руб. - Подтвердить распознанную сумму",
                            callback_data=f"confirm_payment_{user_id}_{city}_{payment_info.amount}",
                        )
                    ]
                )

                # Add custom amount button
                validation_buttons.append(
                    [
                        InlineKeyboardButton(
                            text="✅ Подтвердить другую сумму",
                            callback_data=f"confirm_payment_{user_id}_{city}_custom",
                        )
                    ]
                )
            else:
                # Add standard buttons for different amounts
                if today < EARLY_REGISTRATION_DATE:
                    validation_buttons.append(
                        [
                            InlineKeyboardButton(
                                text=f"✅ {discounted_amount} руб. - Подтвердить оплату по скидке",
                                callback_data=f"confirm_payment_{user_id}_{city}_{discounted_amount}",
                            )
                        ]
                    )

                validation_buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"✅ {regular_amount} руб. - Подтвердить оплату",
                            callback_data=f"confirm_payment_{user_id}_{city}_{regular_amount}",
                        )
                    ]
                )

                if formula_amount > regular_amount:
                    validation_buttons.append(
                        [
                            InlineKeyboardButton(
                                text=f"✅ {formula_amount} руб. - Подтвердить оплату по формуле",
                                callback_data=f"confirm_payment_{user_id}_{city}_{formula_amount}",
                            )
                        ]
                    )

                # Add custom amount button
                validation_buttons.append(
                    [
                        InlineKeyboardButton(
                            text="✅ Подтвердить другую сумму",
                            callback_data=f"confirm_payment_{user_id}_{city}_custom",
                        )
                    ]
                )

            # Add decline button
            validation_buttons.append(
                [
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=f"decline_payment_{user_id}_{city}",
                    )
                ]
            )

            validation_markup = InlineKeyboardMarkup(inline_keyboard=validation_buttons)

            # Send the photo or document with caption containing user info
            if has_photo:
                # Get the photo file_id from the message
                photo = response.photo[-1]  # Get the largest photo

                # Send the photo with caption
                forwarded_msg = await bot.send_photo(
                    chat_id=events_chat_id,
                    photo=photo.file_id,
                    caption=user_info,
                    reply_markup=validation_markup,
                )
            else:  # has_pdf
                # Send the PDF document with caption
                forwarded_msg = await bot.send_document(
                    chat_id=events_chat_id,
                    document=response.document.file_id,
                    caption=user_info,
                    reply_markup=validation_markup,
                )

            # Save the screenshot message ID for reference
            await app.save_payment_info(
                user_id,
                city,
                discounted_amount,
                regular_amount,
                forwarded_msg.message_id,
                formula_amount=formula_amount,
            )

            logger.info(f"Payment proof from user {user_id} sent to validation chat with caption")
        except Exception as e:
            logger.error(f"Error forwarding payment proof to validation chat: {e}")

        # Notify user
        await send_safe(
            message.chat.id,
            "Спасибо за подтверждение оплаты! Ваш платеж находится на проверке. Мы уведомим вас, когда он будет подтвержден.",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        # No screenshot received
        await send_safe(
            message.chat.id,
            "Хорошо! Вы можете оплатить позже, используя команду /pay",
            reply_markup=ReplyKeyboardRemove(),
        )

        # Save payment info with pending status
        await app.save_payment_info(user_id, city, discounted_amount, regular_amount)

    # Return True if payment was processed (screenshot or PDF received)
    return has_photo or has_pdf


async def parse_payment_info(response, has_photo: bool, has_pdf: bool, bot) -> PaymentInfo:
    from app.routers.admin import extract_payment_from_image

    # Get the file
    if has_photo:
        file_id = response.photo[-1].file_id
        file = await bot.get_file(file_id)
        file_bytes = await bot.download_file(file.file_path)
        return await extract_payment_from_image(file_bytes.read(), "image/jpeg")
    elif has_pdf:
        file_id = response.document.file_id
        file = await bot.get_file(file_id)
        file_bytes = await bot.download_file(file.file_path)
        return await extract_payment_from_image(file_bytes.read(), "application/pdf")


# Define Pydantic model for payment information
class PaymentInfo(BaseModel):
    amount: Optional[int]
    is_valid: bool  # Whether there's a clear payment amount in the document

# todo: auto-determine file type from name.
# async def extract_payment_from_image(
#         file_bytes: bytes
# file_name: str
# ) -> PaymentInfo:
# if file_name.endswith(".pdf"):
#     file_type = "application/pdf"
## elif file_name.endswith(".jpg") or file_name.endswith(".jpeg") or file_name.endswith(".png"):
# else:
#     file_type = "image/{file_name.split('.')[-1]}"
async def extract_payment_from_image(
    file_bytes: bytes, file_type: str = "image/jpeg"
) -> PaymentInfo:
    """Extract payment amount from an image or PDF using GPT-4 Vision via litellm"""
    try:
        # Define the system prompt for payment extraction
        system_prompt = """You are a payment receipt analyzer.
        Your task is to extract ONLY the payment amount in rubles from the receipt image or PDF.

        If you cannot determine the amount or if it's ambiguous, set amount to null and is_valid to false."""

        # For images, encode to base64
        encoded_file = base64.b64encode(file_bytes).decode("utf-8")
        if file_type not in ["image/jpeg", "image/png", "application/pdf"]:
            raise ValueError(f"Unsupported file type: {file_type}")

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Please extract the payment amount from this receipt:",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{file_type};base64,{encoded_file}"},
                    },
                ],
            },
        ]

        # Make the API call with the Pydantic model
        response = await acompletion(
            model="claude-3-5-sonnet-20240620",
            messages=messages,
            max_tokens=100,
            response_format=PaymentInfo,
        )

        return PaymentInfo(**json.loads(response.choices[0].message.content))
    except Exception as e:
        logger.error(f"Error extracting payment amount: {e}")
        return PaymentInfo(amount=None, is_valid=False)


@commands_menu.add_command(
    "parse_payment", "Анализ платежа с помощью GPT-4", visibility=Visibility.ADMIN_ONLY
)
# @router.message(Command("parse_payment"), AdminFilter())
async def parse_payment_handler(message: Message, state: FSMContext):
    """Hidden admin command to test payment parsing from images/PDFs"""
    # Ask user to send a payment proof
    response = await ask_user_raw(
        message.chat.id,
        "Отправьте скриншот или PDF с подтверждением платежа для анализа суммы платежа",
        state,
        timeout=300,  # 5 minutes timeout
    )

    if not response:
        await send_safe(message.chat.id, "Время ожидания истекло.")
        return

    # Check if the message has a photo or document
    has_photo = response.photo is not None and len(response.photo) > 0
    has_pdf = response.document is not None and response.document.mime_type == "application/pdf"

    if not (has_photo or has_pdf):
        await send_safe(message.chat.id, "Пожалуйста, отправьте изображение или PDF-файл")
        return

    # Send status message
    status_msg = await send_safe(message.chat.id, "⏳ Анализирую платеж...")

    try:
        # Download the file
        from botspot.core.dependency_manager import get_dependency_manager

        deps = get_dependency_manager()
        bot = deps.bot

        file_id = None
        if has_photo and response.photo:
            # Get the largest photo
            file_id = response.photo[-1].file_id
            file_type = "image/jpeg"
        elif has_pdf and response.document:
            file_id = response.document.file_id
            file_type = "application/pdf"
        else:
            await status_msg.edit_text("❌ Не удалось получить файл")
            return

        if not file_id:
            await status_msg.edit_text("❌ Не удалось получить файл")
            return

        # Download the file
        file = await bot.get_file(file_id)
        if not file or not file.file_path:
            await status_msg.edit_text("❌ Не удалось получить путь к файлу")
            return

        file_bytes = await bot.download_file(file.file_path)
        if not file_bytes:
            await status_msg.edit_text("❌ Не удалось скачать файл")
            return

        # Extract payment information directly from the file
        result = await extract_payment_from_image(file_bytes.read(), file_type)

        # Format the response
        if result.is_valid:
            response_text = f"✅ Обнаружен платеж на сумму: <b>{result.amount}</b> руб."
        else:
            response_text = "❌ Не удалось извлечь сумму платежа"

        # Update the status message with the results
        await status_msg.edit_text(response_text, parse_mode="HTML")

    except Exception as e:
        await status_msg.edit_text(f"❌ Произошла ошибка: {str(e)}")
