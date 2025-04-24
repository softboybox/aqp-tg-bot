from src.bot.telegram_bot import TelegramBot
from src.config.settings import settings
from src.knowledge_base.knowledge_service import ColabKnowledgeService

def main():
    knowledge_service = ColabKnowledgeService()
    bot = TelegramBot(settings.TELEGRAM_TOKEN, knowledge_service)
    bot.run()

if __name__ == "__main__":
    main()