import logging
from abc import ABC, abstractmethod
from src.database.db_connection import DatabaseConnection
from src.config.settings import settings

logger = logging.getLogger(__name__)

class AuthService(ABC):
    @abstractmethod
    def login(self, telegram_id: int, password: str) -> bool:
        pass

    @abstractmethod
    def is_authorized(self, telegram_id: int) -> bool:
        pass

    @abstractmethod
    def is_admin(self, telegram_id: int) -> bool:
        pass

    @abstractmethod
    def logout(self, telegram_id: int) -> bool:
        pass

class PostgresAuthService(AuthService):
    def __init__(self):
        self.db = DatabaseConnection()

    def login(self, telegram_id: int, password: str) -> bool:
        if password != settings.ADMIN_PASSWORD:
            logger.warning(f"Failed login attempt for telegram_id {telegram_id}")
            return False
        try:
            conn = self.db.connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (telegram_id, is_authorized, role)
                    VALUES (%s, TRUE, 'admin')
                    ON CONFLICT (telegram_id)
                    DO UPDATE SET is_authorized = TRUE, role = 'admin'
                    RETURNING id
                    """,
                    (telegram_id,)
                )
                result = cur.fetchone()
                conn.commit()
                logger.info(f"User {telegram_id} logged in successfully")
                return result is not None
        except Exception as e:
            logger.error(f"Error during login for telegram_id {telegram_id}: {e}")
            return False
        finally:
            self.db.close()

    def is_authorized(self, telegram_id: int) -> bool:
        try:
            conn = self.db.connect()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT is_authorized FROM users WHERE telegram_id = %s",
                    (telegram_id,)
                )
                result = cur.fetchone()
                return result['is_authorized'] if result else False
        except Exception as e:
            logger.error(f"Error checking authorization for telegram_id {telegram_id}: {e}")
            return False
        finally:
            self.db.close()

    def is_admin(self, telegram_id: int) -> bool:
        try:
            conn = self.db.connect()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT role FROM users WHERE telegram_id = %s",
                    (telegram_id,)
                )
                result = cur.fetchone()
                return result['role'] == 'admin' if result else False
        except Exception as e:
            logger.error(f"Error checking admin status for telegram_id {telegram_id}: {e}")
            return False
        finally:
            self.db.close()

    def logout(self, telegram_id: int) -> bool:
        try:
            conn = self.db.connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET is_authorized = FALSE, role = 'user'
                    WHERE telegram_id = %s
                    RETURNING telegram_id
                    """,
                    (telegram_id,)
                )
                result = cur.fetchone()
                conn.commit()
                logger.info(f"User {telegram_id} logged out successfully")
                return result is not None
        except Exception as e:
            logger.error(f"Error during logout for telegram_id {telegram_id}: {e}")
            return False
        finally:
            self.db.close()