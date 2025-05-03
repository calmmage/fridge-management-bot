# Fridge Management App - Core Features

## Tech Stack
- Botspot core utils
  - Single user mode
  - LLM provider integration
  - MongoDB storage
- Aiogram for Telegram bot
- Image handling from bot-146
  - Download from Telegram
  - Upload for AI processing

## Image Processing Integration
1) adapt the code to use botspot.llm_provider
2) add those utils to botspot - nicely.
a) to llm provider query_llm add a feature to accept aiogram files
b) add a util to download aiogram file - using aiogram or pyrogram client if the file is too big
c) in our llm provider - check whether the file data or the aiogram / pyrogram file reference is provided. if latter -  auto-download the file.

## MVP Implementation
- Product addition flows:
  - Text input → AI structured parsing → User validation → AI correction
  - Photo input → AI extraction → User validation → AI correction
- Basic commands:
  - /add_text - add product with text
  - /add_photo - add product with photo
  - /list - view current products

## Optional Product Attributes
- Opened/unopened status
- Opening date tracking
- Fridge location

## Future Features
- "Expiring soon" reminders
- Bulk text updates
- Quick sorting mode 