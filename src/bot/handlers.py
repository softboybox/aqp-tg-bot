import uuid
import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes
from src.knowledge_base.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Вітаємо в чат-боті Aquapolis! Напишіть, що вас цікавить")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    query = update.message.text
    session_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"telegram-user-{user_id}"))
    logger.info(f"Processing query from user {user_id}: {query}")

    knowledge_service: KnowledgeService = context.bot_data["knowledge_service"]
    loop = asyncio.get_event_loop()
    query_task = loop.run_in_executor(
        None,
        lambda: knowledge_service.process_query(query, session_id)
    )

    # Периодически отправляем действие "Печатает..." пока запрос обрабатывается
    while not query_task.done():
        await update.message.chat.send_action("typing")
        await asyncio.sleep(4)

    try:
        response = await query_task

        await update.message.chat.send_action("typing")
        try:
            await update.message.reply_text(response, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send response: {e}")
            await update.message.reply_text(response)

    except Exception as e:
        logger.error(f"Error processing query for user {user_id}: {e}")
        await update.message.reply_text("Вибачте, сталася помилка. Спробуйте ще раз.")