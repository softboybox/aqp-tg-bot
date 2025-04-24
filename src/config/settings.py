import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    DB_CONFIG = {
        "host": os.getenv("DB_HOST"),
        "port": os.getenv("DB_PORT"),
        "database": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
    }
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    PDF_FILES_PATH = os.getenv("PDF_FILES_PATH")
    FAISS_INDEX_PATH = os.getenv("FAISS_INDEX_PATH")
    LC_CHAT_HISTORY_TABLE_NAME = os.getenv("LC_CHAT_HISTORY_TABLE_NAME")
    LC_DATABASE_URL = f"postgresql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"

settings = Settings()