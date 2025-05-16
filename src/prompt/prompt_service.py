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

    @abstractmethod
    def validate_prompt(self, prompt: str) -> bool:
        pass


class PostgresPromptService(PromptService):
    def __init__(self):
        self.db = DatabaseConnection()

    def get_current_prompt(self) -> str:
        logger.info("Fetching current prompt from database")
        try:
            conn = self.db.connect()
            with conn.cursor() as cur:
                logger.debug("Executing SQL: SELECT prompt_text FROM system_prompts ORDER BY updated_at DESC LIMIT 1")
                cur.execute(
                    "SELECT prompt_text FROM system_prompts ORDER BY updated_at DESC LIMIT 1"
                )
                result = cur.fetchone()
                if result:
                    prompt = result['prompt_text']
                    logger.info(f"Retrieved prompt: {prompt[:100]}... (length: {len(prompt)})")
                    return prompt
                logger.info("No prompt found in database, returning INITIAL_SYSTEM_PROMPT")
                return settings.INITIAL_SYSTEM_PROMPT
        except Exception as e:
            logger.error(f"Error retrieving prompt: {e}")
            logger.info("Returning INITIAL_SYSTEM_PROMPT due to error")
            return settings.INITIAL_SYSTEM_PROMPT
        finally:
            self.db.close()

    def update_prompt(self, new_prompt: str) -> bool:
        logger.info(f"Updating prompt: {new_prompt[:100]}... (length: {len(new_prompt)})")
        try:
            # Проверяем валидность промта перед сохранением
            if not self.validate_prompt(new_prompt):
                logger.error("Prompt validation failed")
                return False

            conn = self.db.connect()
            with conn.cursor() as cur:
                logger.debug(
                    "Executing SQL: INSERT INTO system_prompts (prompt_text, updated_at) VALUES (%s, CURRENT_TIMESTAMP) RETURNING id")
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
                if result:
                    logger.info(f"Prompt successfully inserted, ID: {result['id']}")
                    return True
                logger.error("No ID returned after inserting prompt")
                return False
        except Exception as e:
            logger.error(f"Error updating prompt: {e}")
            return False
        finally:
            self.db.close()

    def validate_prompt(self, prompt: str) -> bool:

        # Проверка на пустой промт
        if not prompt or prompt.strip() == '"""':
            logger.error("Empty prompt")
            return False

        # Проверка на наличие {context}
        if "{context}" not in prompt:
            logger.warning("Prompt doesn't contain {context} placeholder")
            # Это не критическая ошибка, т.к. мы добавляем {context} автоматически если его нет

        # Проверка на правильное форматирование с тройными кавычками
        if not (prompt.strip().startswith('"""') and prompt.strip().endswith('"""')):
            logger.warning("Prompt doesn't have proper triple quotes formatting")
            # Это не критическая ошибка, т.к. мы добавляем форматирование автоматически

        return True

    def sync_initial_prompt(self) -> bool:
        logger.info("Syncing initial system prompt to database")
        try:
            conn = self.db.connect()
            with conn.cursor() as cur:
                logger.debug("Executing SQL: INSERT INTO system_prompts (prompt_text, created_at, updated_at) ...")
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
                if result:
                    logger.info(f"Initial system prompt synced, ID: {result['id']}")
                    return True
                logger.error("No ID returned after syncing initial prompt")
                return False
        except Exception as e:
            logger.error(f"Error syncing initial prompt: {e}")
            return False
        finally:
            self.db.close()