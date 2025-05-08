import logging
from src.bot.telegram_bot import TelegramBot
from src.config.settings import settings
from src.knowledge_base.knowledge_service import ColabKnowledgeService
from src.auth.auth_service import PostgresAuthService
from src.prompt.prompt_service import PostgresPromptService

logger = logging.getLogger(__name__)

def main():
    prompt_service = PostgresPromptService()

    if not prompt_service.sync_initial_prompt():
        logger.error("Failed to sync initial prompt.")
        return
    knowledge_service = ColabKnowledgeService()
    auth_service = PostgresAuthService()
    bot = TelegramBot(settings.TELEGRAM_TOKEN, knowledge_service, auth_service)
    bot.run()

if __name__ == "__main__":
    main()