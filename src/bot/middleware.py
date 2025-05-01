from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes, CallbackContext
from src.auth.auth_service import AuthService

def admin_required(handler):
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        auth_service: AuthService = context.bot_data["auth_service"]
        telegram_id = update.effective_user.id
        if not auth_service.is_authorized(telegram_id) or not auth_service.is_admin(telegram_id):
            await update.message.reply_text("Ви не авторизовані або не маєте прав адміністратора.")
            return
        return await handler(update, context)
    return wrapper