import uuid
import asyncio
import logging
import os
import time
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, constants
from telegram.ext import ContextTypes, ConversationHandler
from src.knowledge_base.knowledge_service import KnowledgeService
from src.auth.auth_service import AuthService
from src.bot.middleware import admin_required
from src.bot.states import BotState, WAITING_CSV
from src.prompt.prompt_service import PostgresPromptService
from src.config.settings import settings

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


def get_admin_keyboard():
    return [
        [KeyboardButton("Повернутися до помічника"), KeyboardButton("Редагувати промт")],
        [KeyboardButton("Переглянути промт"), KeyboardButton("Очистити історію")],
        [KeyboardButton("Завантажити CSV"), KeyboardButton("Статус бази знань")]
    ]


async def _request_new_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Requesting new prompt from user {update.effective_user.id}")
    context.user_data[BotState.AWAITING_PROMPT.value] = True
    context.user_data["prompt_parts"] = []
    await update.message.reply_text(
        "Будь ласка, введіть новий промт (якщо промт довгий, відправляйте його частинами послідовно):",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("Зберегти промт")], [KeyboardButton("Скасувати")]],
            one_time_keyboard=True,
            resize_keyboard=True
        )
    )


@admin_required
async def kb_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"CSV upload requested by user {update.effective_user.id}")
    
    context.user_data[BotState.AWAITING_CSV_UPLOAD.value] = True
    
    await update.message.reply_text(
        "📎 Відправте CSV файл для оновлення бази знань.\n\n"
        f"⚠️ Максимальний розмір: {settings.MAX_CSV_SIZE_MB} MB\n"
        "Підтримувані формати: .csv файли\n\n"
        "Натисніть 'Скасувати' для відміни.",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("Скасувати")]],
            one_time_keyboard=True,
            resize_keyboard=True
        )
    )


async def handle_csv_document_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    
    if not doc:
        await update.message.reply_text("❌ Відправте CSV файл або натисніть 'Скасувати'.")
        return

    mime_type = (doc.mime_type or "").lower()
    file_name = (doc.file_name or "").lower()
    
    if not (mime_type == "text/csv" or file_name.endswith(".csv")):
        await update.message.reply_text(
            "❌ Очікую CSV файл (text/csv або .csv).\n"
            "Відправте правильний файл або натисніть 'Скасувати'."
        )
        return

    # Проверка размера файла
    file_size_mb = doc.file_size / (1024 * 1024) if doc.file_size else 0
    if file_size_mb > settings.MAX_CSV_SIZE_MB:
        await update.message.reply_text(
            f"❌ Файл завеликий ({file_size_mb:.1f} MB).\n"
            f"Максимальний розмір: {settings.MAX_CSV_SIZE_MB} MB"
        )
        return

    os.makedirs(settings.TEMP_CSV_DIR, exist_ok=True)
    
    timestamp = int(time.time())
    safe_filename = f"{timestamp}_{doc.file_name or 'knowledge_base.csv'}"
    temp_path = os.path.join(settings.TEMP_CSV_DIR, safe_filename)
    
    try:
        await update.message.chat.send_action(constants.ChatAction.UPLOAD_DOCUMENT)
        await update.message.reply_text("⏳ Завантажую файл...")
        
        file = await doc.get_file()
        await file.download_to_drive(temp_path)
        
        logger.info(f"CSV file downloaded: {temp_path}")
        
        await update.message.reply_text(
            "⏳ Обробляю файл та перебудовую індекс...\n"
            "Це може зайняти кілька хвилин."
        )
        
        knowledge_service: KnowledgeService = context.bot_data["knowledge_service"]
        
        ok, message, meta = await knowledge_service.update_knowledge_base(temp_path)
        
        context.user_data.pop(BotState.AWAITING_CSV_UPLOAD.value, None)
        
        if ok:
            response_text = (
                f"✅ {message}\n\n"
                f"📄 Файл: `{os.path.basename(meta.get('csv_path', ''))}`\n"
                f"📊 Строк: *{meta.get('row_count', 0)}*\n"
                f"⏰ Створено: `{meta.get('built_at', 'невідомо')}`"
            )
            
            await update.message.reply_text(
                response_text,
                parse_mode=constants.ParseMode.MARKDOWN
            )
            
            # Возврат к админ-меню
            reply_keyboard = get_admin_keyboard()
            await update.message.reply_text(
                "🎉 База знань успішно оновлена! Оберіть наступну дію:",
                reply_markup=ReplyKeyboardMarkup(
                    reply_keyboard, 
                    one_time_keyboard=True, 
                    resize_keyboard=True
                )
            )
        else:
            # Ошибка обновления
            await update.message.reply_text(f"❌ Помилка: {message}")
            
            reply_keyboard = get_admin_keyboard()
            await update.message.reply_text(
                "Оберіть дію:",
                reply_markup=ReplyKeyboardMarkup(
                    reply_keyboard, 
                    one_time_keyboard=True, 
                    resize_keyboard=True
                )
            )
            
    except Exception as e:
        logger.error(f"Error processing CSV upload: {e}")
        context.user_data.pop(BotState.AWAITING_CSV_UPLOAD.value, None)
        
        reply_keyboard = get_admin_keyboard()
        await update.message.reply_text(
            f"❌ Виникла помилка при обробці файлу: {str(e)}",
            reply_markup=ReplyKeyboardMarkup(
                reply_keyboard, 
                one_time_keyboard=True, 
                resize_keyboard=True
            )
        )
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
                logger.info(f"Temporary file removed: {temp_path}")
        except Exception as e:
            logger.warning(f"Failed to remove temp file {temp_path}: {e}")


async def handle_csv_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await handle_csv_document_upload(update, context)


async def cancel_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"CSV upload cancelled by user {update.effective_user.id}")
    
    context.user_data.pop(BotState.AWAITING_CSV_UPLOAD.value, None)
    
    reply_keyboard = get_admin_keyboard()
    await update.message.reply_text(
        "❌ Завантаження скасовано.",
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard, 
            one_time_keyboard=True, 
            resize_keyboard=True
        )
    )
    return ConversationHandler.END


@admin_required
async def kb_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Knowledge base status requested by user {update.effective_user.id}")
    
    try:
        knowledge_service: KnowledgeService = context.bot_data["knowledge_service"]
        meta = knowledge_service.get_knowledge_base_status()
        
        if not meta.get("csv_path"):
            if os.path.exists(settings.CSV_FILE_PATH):
                file_size = os.path.getsize(settings.CSV_FILE_PATH)
                file_mtime = os.path.getmtime(settings.CSV_FILE_PATH)
                from datetime import datetime
                mtime_str = datetime.utcfromtimestamp(file_mtime).isoformat(timespec="seconds") + "Z"
                
                status_text = (
                    "📚 *Статус бази знань*\n\n"
                    f"📄 Файл: `{os.path.basename(settings.CSV_FILE_PATH)}`\n"
                    f"📏 Розмір: *{file_size / 1024:.1f} KB*\n"
                    f"📅 Змінено: `{mtime_str}`\n"
                    f"⚠️ Індекс не створювався через завантаження"
                )
            else:
                status_text = "ℹ️ CSV файл не знайдено. Завантажте файл через 'Завантажити CSV'."
        else:
            # Форматирование статуса из метаданных
            csv_name = os.path.basename(meta.get("csv_path", ""))
            row_count = meta.get("row_count", 0)
            built_at = meta.get("built_at", "невідомо")
            csv_mtime = meta.get("csv_mtime", "невідомо")
            checksum = str(meta.get("checksum", ""))[:12] + "..." if meta.get("checksum") else "невідомо"
            
            status_text = (
                "📚 *Статус бази знань*\n\n"
                f"📄 Файл: `{csv_name}`\n"
                f"📊 Строк: *{row_count}*\n"
                f"🔨 Індекс створено: `{built_at}`\n"
                f"📅 CSV змінено: `{csv_mtime}`\n"
            )
        
        await update.message.reply_text(
            status_text,
            parse_mode=constants.ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"Error getting knowledge base status: {e}")
        await update.message.reply_text(
            "❌ Виникла помилка при отриманні статусу бази знань."
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

    if query == "Скасувати" and context.user_data.get(BotState.AWAITING_CSV_UPLOAD.value):
        context.user_data.pop(BotState.AWAITING_CSV_UPLOAD.value, None)
        reply_keyboard = get_admin_keyboard()
        await update.message.reply_text(
            "❌ Завантаження CSV скасовано.",
            reply_markup=ReplyKeyboardMarkup(
                reply_keyboard, 
                one_time_keyboard=True, 
                resize_keyboard=True
            )
        )
        return

    if query == "Завантажити CSV":
        await kb_upload(update, context)
        return
        
    if query == "Статус бази знань":
        await kb_status(update, context)
        return

    # Обработка кнопки "Редагувати промт"
    if query == "Редагувати промт":
        await _request_new_prompt(update, context)
        return

    # Обработка кнопки "Переглянути промт"
    if query == "Переглянути промт":
        prompt_service = PostgresPromptService()
        current_prompt = prompt_service.get_current_prompt()
        reply_keyboard = get_admin_keyboard()
        
        if len(current_prompt) > 4000:
            parts = [current_prompt[i:i + 4000] for i in range(0, len(current_prompt), 4000)]
            for part in parts:
                await update.message.reply_text(f"Частина промпту:\n\n{part}")
        else:
            await update.message.reply_text(f"Поточний промт:\n\n{current_prompt}")
        await update.message.reply_text(
            "Оберіть дію:",
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
            reply_keyboard = get_admin_keyboard()
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

    # Обработка режима сбора многочастного промта
    if context.user_data.get(BotState.AWAITING_PROMPT.value):
        # Если получена команда сохранения промта
        if query == "Зберегти промт":
            prompt_parts = context.user_data.get("prompt_parts", [])
            if not prompt_parts:
                await update.message.reply_text(
                    "Немає жодної частини промту для збереження. Будь ласка, спочатку введіть промт.",
                    reply_markup=ReplyKeyboardMarkup(
                        [[KeyboardButton("Скасувати")]],
                        one_time_keyboard=True,
                        resize_keyboard=True
                    )
                )
                return

            # Собираем все части промта в один
            complete_prompt = "\n".join(prompt_parts)

            # Форматируем промт и добавляем {context} если отсутствует
            formatted_prompt = f'"""\n{complete_prompt}\n"""'
            if "{context}" not in complete_prompt:
                formatted_prompt = f'"""\n{complete_prompt}\n{{context}}\n"""'

            knowledge_service: KnowledgeService = context.bot_data["knowledge_service"]
            try:
                if knowledge_service.update_prompt(formatted_prompt):
                    context.user_data.clear()
                    reply_keyboard = get_admin_keyboard()
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

        # Если получена команда отмены
        if query == "Скасувати":
            context.user_data.clear()
            reply_keyboard = get_admin_keyboard()
            await update.message.reply_text(
                "Редагування промту скасовано.",
                reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
            )
            return

        # Добавляем часть промта в список и продолжаем сбор
        if "prompt_parts" not in context.user_data:
            context.user_data["prompt_parts"] = []

        context.user_data["prompt_parts"].append(query)

        # Показываем сколько частей уже получено
        parts_count = len(context.user_data["prompt_parts"])
        await update.message.reply_text(
            f"Отримано частину промту #{parts_count}. Продовжуйте вводити наступну частину або натисніть 'Зберегти промт', якщо всі частини введено.",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("Зберегти промт")], [KeyboardButton("Скасувати")]],
                one_time_keyboard=True,
                resize_keyboard=True
            )
        )
        return

    if auth_service.is_authorized(user_id) and auth_service.is_admin(user_id):
        logger.info(f"User {user_id} is admin: {auth_service.is_admin(user_id)}")
        reply_keyboard = get_admin_keyboard()
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


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get(BotState.AWAITING_CSV_UPLOAD.value):
        await handle_csv_document_upload(update, context)
        return
    
    # Иначе игнорируем документ
    await update.message.reply_text("Документ проігноровано. Використовуйте команди бота.")


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
        reply_keyboard = get_admin_keyboard()
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
    session_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"telegram-user-{user_id}"))
    logger.info(f"Clearing history for user {user_id} with session_id {session_id}")

    try:
        knowledge_service: KnowledgeService = context.bot_data["knowledge_service"]
        if knowledge_service.clear_history(session_id):
            logger.info(f"History cleared for user {user_id}")
            await update.message.reply_text("Історія успішно видалена!")
            await start(update, context)
        else:
            logger.error(f"Failed to clear history for user {user_id}")
            await update.message.reply_text("Вибачте, сталася помилка при очищенні історії. Спробуйте ще раз.")
    except Exception as e:
        logger.error(f"Failed to clear history for user {user_id}: {e}")
        await update.message.reply_text("Вибачте, сталася помилка. Спробуйте ще раз.")