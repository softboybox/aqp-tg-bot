import logging
from abc import ABC, abstractmethod
from src.database.db_connection import DatabaseConnection
from src.config.settings import settings

logger = logging.getLogger(__name__)

class PromptService(ABC):
    @abstractmethod
    def get_current_prompt(self) -> str:
        pass

    @abstractmethod
    def update_prompt(self, new_prompt: str) -> bool:
        pass

class PostgresPromptService(PromptService):
    def __init__(self):
        self.db = DatabaseConnection()

    def get_current_prompt(self) -> str:
        try:
            conn = self.db.connect()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT prompt_text FROM system_prompts ORDER BY updated_at DESC LIMIT 1"
                )
                result = cur.fetchone()
                return result['prompt_text'] if result else settings.INITIAL_SYSTEM_PROMPT
        except Exception as e:
            logger.error(f"Error retrieving prompt: {e}")
            return settings.INITIAL_SYSTEM_PROMPT
        finally:
            self.db.close()

    def update_prompt(self, new_prompt: str) -> bool:
        try:
            conn = self.db.connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO system_prompts (prompt_text, updated_at)
                    VALUES (%s, CURRENT_TIMESTAMP)
                    RETURNING id
                    """,
                    (new_prompt,)
                )
                result = cur.fetchone()
                conn.commit()
                return result is not None
        except Exception as e:
            logger.error(f"Error updating prompt: {e}")
            return False
        finally:
            self.db.close()

    def sync_initial_prompt(self) -> bool:

        try:
            conn = self.db.connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO system_prompts (prompt_text, created_at, updated_at)
                    VALUES (%s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (id)
                    DO UPDATE SET
                        prompt_text = EXCLUDED.prompt_text,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id
                    """,
                    (settings.INITIAL_SYSTEM_PROMPT,)
                )
                result = cur.fetchone()
                conn.commit()
                logger.info("Initial system prompt synced to database")
                return result is not None
        except Exception as e:
            logger.error(f"Error syncing initial prompt: {e}")
            return False
        finally:
            self.db.close()