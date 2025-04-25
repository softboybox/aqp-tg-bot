from telegram.ext import Application, CommandHandler, MessageHandler, filters
from src.bot.handlers import start, handle_message, login, change_prompt
from src.knowledge_base.knowledge_service import KnowledgeService
from src.auth.auth_service import AuthService, PostgresAuthService

class TelegramBot:
    def __init__(self, token: str, knowledge_service: KnowledgeService, auth_service: AuthService):
        self.app = Application.builder().token(token).build()
        self.knowledge_service = knowledge_service
        self.auth_service = auth_service

    def setup(self):
        self.app.bot_data["knowledge_service"] = self.knowledge_service
        self.app.bot_data["auth_service"] = self.auth_service
        self.app.add_handler(CommandHandler("start", start))
        self.app.add_handler(CommandHandler("login", login))
        self.app.add_handler(CommandHandler("change_prompt", change_prompt))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    def run(self):
        self.setup()
        self.app.run_polling()