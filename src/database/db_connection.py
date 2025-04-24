import psycopg2
from psycopg2.extras import RealDictCursor
from src.config.settings import settings

class DatabaseConnection:
    def __init__(self):
        self.config = settings.DB_CONFIG
        self.connection = None

    def connect(self):
        try:
            self.connection = psycopg2.connect(**self.config, cursor_factory=RealDictCursor)
            return self.connection
        except Exception as e:
            raise Exception(f"Database connection failed: {e}")

    def close(self):
        if self.connection:
            self.connection.close()