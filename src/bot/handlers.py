import uuid
import asyncio
import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from src.knowledge_base.knowledge_service import KnowledgeService
from src.auth.auth_service import AuthService
from src.bot.middleware import admin_required
from src.bot.states import BotState
from src.prompt.prompt_service import PostgresPromptService

logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    auth_service: AuthService = context.bot_data["auth_service"]
    auth_service.logout(user_id)
    context.user_data.clear()
    await update.message.reply_text(
        "Вітаємо в чат-боті Aquapolis! Напишіть, що вас цікавить",
        reply_markup=ReplyKeyboardRemove()
    )

async def _request_new_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Requesting new prompt from user {update.effective_user.id}")
    context.user_data[BotState.AWAITING_PROMPT.value] = True
    await update.message.reply_text(
        "Будь ласка, введіть новий промт:",
        reply_markup=ReplyKeyboardRemove()
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    query = update.message.text
    session_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"telegram-user-{user_id}"))
    logger.info(f"Processing message from user {user_id}: {query}")

    auth_service: AuthService = context.bot_data["auth_service"]

    if query == "Повернутися до помічника":
        logger.info(f"User {user_id} requested to return to assistant")
        auth_service.logout(user_id)
        context.user_data.clear()
        await start(update, context)
        return

    # Обработка кнопки "Редагувати промт"
    if query == "Редагувати промт":
        await _request_new_prompt(update, context)
        return

    # Обработка кнопки "Переглянути промт"
    if query == "Переглянути промт":
        prompt_service = PostgresPromptService()
        current_prompt = prompt_service.get_current_prompt()
        display_prompt = current_prompt[:4000] + ("..." if len(current_prompt) > 4000 else "")
        reply_keyboard = [
            [KeyboardButton("Редагувати промт"), KeyboardButton("Переглянути промт")],
            [KeyboardButton("Повернутися до помічника"), KeyboardButton("Очистити історію")]
        ]
        await update.message.reply_text(
            f"Поточний промт:\n\n{display_prompt}",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return

    # Обработка кнопки "Очистити історію"
    if query == "Очистити історію":
        logger.info(f"Calling clear_history for user {user_id}")
        await clear_history(update, context)
        return

    if context.user_data.get(BotState.AWAITING_PASSWORD.value):
        if auth_service.login(user_id, query):
            context.user_data.clear()
            logger.info(f"User {user_id} logged in as admin: {auth_service.is_admin(user_id)}")
            reply_keyboard = [
                [KeyboardButton("Повернутися до помічника"), KeyboardButton("Редагувати промт")],
                [KeyboardButton("Переглянути промт"), KeyboardButton("Очистити історію")]
            ]
            await update.message.reply_text(
                "Авторизація успішна! Ви отримали права адміністратора.",
                reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
            )
            return
        else:
            reply_keyboard = [[KeyboardButton("Повернутися до помічника")]]
            await update.message.reply_text(
                "Невірний пароль. Спробуйте ще раз або поверніться до помічника.",
                reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
            )
            return

    if context.user_data.get(BotState.AWAITING_PROMPT.value):
        formatted_prompt = f'"""\n{query}\n"""'
        if "{context}" not in query:
            formatted_prompt = f'"""\n{query}\n{{context}}\n"""'
        knowledge_service: KnowledgeService = context.bot_data["knowledge_service"]
        try:
            if knowledge_service.update_prompt(formatted_prompt):
                context.user_data.clear()
                reply_keyboard = [
                    [KeyboardButton("Повернутися до помічника"), KeyboardButton("Редагувати промт")],
                    [KeyboardButton("Переглянути промт"), KeyboardButton("Очистити історію")]
                ]
                await update.message.reply_text(
                    "Промт успішно оновлено!",
                    reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
                )
            else:
                reply_keyboard = [[KeyboardButton("Повернутися до помічника")]]
                await update.message.reply_text(
                    "Помилка при оновленні промпта. Перевірте формат промпта і спробуйте ще раз.",
                    reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
                )
        except Exception as e:
            logger.error(f"Failed to update prompt for user {user_id}: {e}")
            reply_keyboard = [[KeyboardButton("Повернутися до помічника")]]
            await update.message.reply_text(
                f"Виникла помилка при оновленні промпта: {str(e)}. Спробуйте ще раз.",
                reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
            )
        return

    if auth_service.is_authorized(user_id) and auth_service.is_admin(user_id):
        logger.info(f"User {user_id} is admin: {auth_service.is_admin(user_id)}")
        reply_keyboard = [
            [KeyboardButton("Повернутися до помічника"), KeyboardButton("Редагувати промт")],
            [KeyboardButton("Переглянути промт"), KeyboardButton("Очистити історію")]
        ]
        await update.message.reply_text(
            "Будь ласка, оберіть дію:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return

    knowledge_service: KnowledgeService = context.bot_data["knowledge_service"]
    loop = asyncio.get_event_loop()
    query_task = loop.run_in_executor(
        None,
        lambda: knowledge_service.process_query(query, session_id)
    )

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

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    args = context.args
    if not args:
        context.user_data[BotState.AWAITING_PASSWORD.value] = True
        await update.message.reply_text(
            "Будь ласка, вкажіть пароль:",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    password = args[0]
    auth_service: AuthService = context.bot_data["auth_service"]
    if auth_service.login(telegram_id, password):
        context.user_data.clear()
        logger.info(f"User {telegram_id} logged in as admin: {auth_service.is_admin(telegram_id)}")
        reply_keyboard = [
            [KeyboardButton("Повернутися до помічника"), KeyboardButton("Редагувати промт")],
            [KeyboardButton("Переглянути промт"), KeyboardButton("Очистити історію")]
        ]
        await update.message.reply_text(
            "Авторизація успішна! Ви отримали права адміністратора.",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
    else:
        reply_keyboard = [[KeyboardButton("Повернутися до помічника")]]
        context.user_data[BotState.AWAITING_PASSWORD.value] = True
        await update.message.reply_text(
            "Невірний пароль. Спробуйте ще раз або поверніться до помічника.",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
        )

@admin_required
async def change_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _request_new_prompt(update, context)

@admin_required
async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"telegram-user-{user_id}")
    logger.info(f"Clearing history for user {user_id} with session_id {session_id}")

    try:
        knowledge_service: KnowledgeService = context.bot_data["knowledge_service"]
        conn = knowledge_service.assistant.postgres_conn
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM langchain_chat_history WHERE session_id = %s",
            (session_id,)
        )
        deleted_rows = cursor.rowcount
        conn.commit()
        cursor.close()
        logger.info(f"History cleared for user {user_id}, deleted {deleted_rows} rows")

        await update.message.reply_text("Історія успішно видалена!")
        await start(update, context)
    except Exception as e:
        logger.error(f"Failed to clear history for user {user_id}: {e}")
        await update.message.reply_text("Вибачте, сталася помилка. Спробуйте ще раз.")
