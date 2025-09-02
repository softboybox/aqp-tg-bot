from telegram.ext import Application, CommandHandler, MessageHandler, ConversationHandler, filters
from src.bot.handlers import (
    start, handle_message, handle_document, login, change_prompt, clear_history,
    kb_upload, kb_status, handle_csv_document, cancel_upload
)
from src.bot.states import WAITING_CSV
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
        self.app.add_handler(CommandHandler("clear_history", clear_history))
        self.app.add_handler(CommandHandler("kb_status", kb_status))
        
        csv_conversation_handler = ConversationHandler(
            entry_points=[CommandHandler("kb_upload", kb_upload)],
            states={
                WAITING_CSV: [MessageHandler(
                    filters.Document.MimeType("text/csv") | filters.Document.FileExtension("csv"),
                    handle_csv_document
                )]
            },
            fallbacks=[CommandHandler("cancel", cancel_upload)],
            allow_reentry=True,
        )
        self.app.add_handler(csv_conversation_handler)
        
        self.app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    def run(self):
        self.setup()
        self.app.run_polling()