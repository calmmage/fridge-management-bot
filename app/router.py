from aiogram import Router, html
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from botspot import commands_menu
from botspot.components.new.llm_provider import aquery_llm_text
from botspot.utils import send_safe

from app._app import App
from botspot.utils.unsorted import get_message_attachments

router = Router()


@commands_menu.botspot_command("start", "Start the bot")
@router.message(CommandStart())
async def start_handler(message: Message, app: App):
    await send_safe(
        message.chat.id,
        f"Hello, {html.bold(message.from_user.full_name)}!\n"
        f"Welcome to {app.name}!\n"
        f"Use /help to see available commands.",
    )


@commands_menu.botspot_command("help", "Show this help message")
@router.message(Command("help"))
async def help_handler(message: Message, app: App):
    """Basic help command handler"""
    await send_safe(
        message.chat.id,
        f"This is {app.name}. Use /start to begin."
        "Available commands:\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/help_botspot - Show Botspot help\n"
        "/timezone - Set your timezone\n"
        "/error_test - Test error handling",
    )


# test chat with gpt with file attachments
@router.message()
async def handle_message(message: Message):
    # import litellm
    # litellm._turn_on_debug()

    attachments = get_message_attachments(message) or None

    # llm_provider = get_llm_provider()

    response = await aquery_llm_text(
        prompt=message.text or message.caption,
        # model="gpt-4o",
        model="claude-3.5",
        attachments=attachments,
    )
    await send_safe(message.chat.id, f"{response}")
