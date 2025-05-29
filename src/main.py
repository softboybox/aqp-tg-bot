import os
from src.bot.telegram_bot import TelegramBot
from src.config.settings import settings
from src.knowledge_base.knowledge_service_optimized_fixed import OptimizedColabKnowledgeService
from src.auth.auth_service import PostgresAuthService
from src.prompt.prompt_service import PostgresPromptService
import logging

logger = logging.getLogger(__name__)


def ensure_directories_exist():
    directories = [
        os.path.dirname(settings.CSV_FILE_PATH),
        settings.FAISS_INDEX_PATH,
    ]

    for directory in directories:
        if not os.path.exists(directory):
            logger.info(f"Creating directory: {directory}")
            os.makedirs(directory, exist_ok=True)


def main():
    ensure_directories_exist()

    prompt_service = PostgresPromptService()

    if not prompt_service.sync_initial_prompt():
        logger.error("Failed to sync initial prompt.")
        return

    knowledge_service = OptimizedColabKnowledgeService()
    auth_service = PostgresAuthService()
    bot = TelegramBot(settings.TELEGRAM_TOKEN, knowledge_service, auth_service)
    bot.run()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    main()
