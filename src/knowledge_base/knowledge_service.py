import os
import logging
import psycopg
import uuid
from abc import ABC, abstractmethod
from langchain_community.document_loaders import CSVLoader
from langchain.text_splitter import CharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from typing import List
from src.prompt.prompt_service import PromptService, PostgresPromptService
from src.config.settings import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_CONTEXT_LENGTH = 4000


class KnowledgeService(ABC):
    @abstractmethod
    def process_query(self, query: str, session_id: str) -> str:
        pass

    @abstractmethod
    def update_prompt(self, new_prompt: str) -> bool:
        pass

    @abstractmethod
    def clear_history(self, session_id: str) -> bool:
        pass


class CustomPostgresChatMessageHistory(BaseChatMessageHistory):

    def __init__(self, table_name: str, session_id: str, connection):
        self.table_name = table_name
        self.session_id = session_id
        self.connection = connection

    @property
    def messages(self) -> List[BaseMessage]:
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                f"SELECT type, content FROM {self.table_name} WHERE session_id = %s ORDER BY created_at",
                (self.session_id,)
            )
            rows = cursor.fetchall()
            cursor.close()

            messages = []
            for msg_type, content in rows:
                if msg_type == 'human':
                    messages.append(HumanMessage(content=content))
                elif msg_type == 'ai':
                    messages.append(AIMessage(content=content))

            return messages
        except Exception as e:
            logger.warning(f"Failed to load messages: {e}")
            return []

    def add_message(self, message: BaseMessage) -> None:
        try:
            cursor = self.connection.cursor()
            msg_type = 'human' if isinstance(message, HumanMessage) else 'ai'
            cursor.execute(
                f"INSERT INTO {self.table_name} (session_id, type, content) VALUES (%s, %s, %s)",
                (self.session_id, msg_type, message.content)
            )
            self.connection.commit()
            cursor.close()
        except Exception as e:
            logger.warning(f"Failed to add message: {e}")

    def clear(self) -> None:
        """Очистка истории для данной сессии"""
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                f"DELETE FROM {self.table_name} WHERE session_id = %s",
                (self.session_id,)
            )
            self.connection.commit()
            cursor.close()
        except Exception as e:
            logger.warning(f"Failed to clear messages: {e}")


class OptimizedAQPAssistant:

    def __init__(self, file_path, prompt_service: PromptService):
        os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY
        self.retriever = self.vectorize_content(file_path)
        self.prompt_service = prompt_service

        self.unified_prompt = self.create_unified_prompt()

        self.llm = ChatOpenAI(
            model="chatgpt-4o-latest",
            temperature=0,
            max_retries=2,
            request_timeout=120
        )

        self.rag_chain = self.create_simple_rag_chain()

        self.postgres_conn = psycopg.connect(settings.LC_DATABASE_URL)
        self.postgres_table_name = settings.LC_CHAT_HISTORY_TABLE_NAME

        self._ensure_table_exists()

    def _ensure_table_exists(self):
        try:
            cursor = self.postgres_conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS langchain_chat_history (
                    id SERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    content TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_langchain_chat_history_session_id 
                ON langchain_chat_history (session_id)
            """)
            self.postgres_conn.commit()
            cursor.close()
            logger.info(f"PostgreSQL chat history table '{self.postgres_table_name}' ready")
        except Exception as e:
            logger.warning(f"Could not create chat history table: {e}")

    def create_unified_prompt(self):
        """Создает ЕДИНЫЙ промпт, который выполняет всю логику за ОДИН запрос"""
        base_prompt = self.prompt_service.get_current_prompt()

        unified_prompt = f"""
        Ты експерт з хімії для басейнів і спеціалізуєшся на підборі продукції бренду AquaDoctor.

        Твоя задача за ОДИН запит:
        1. Визначити чи це питання про хімію для басейну
        2. Якщо ТАК - підібрати потрібні препарати AquaDoctor (максимум 4)
        3. Для кожного препарату знайти дозування та фасування з контексту
        4. Дати повну відповідь з розрахунками

        Якщо це НЕ питання про басейни/хімію - відповідай згідно базового промпта:
        {base_prompt}

        ВАЖЛИВО: Використовуй весь доступний контекст для пошуку інформації про продукти та їх дозування.
        Не роби окремі запити - все має бути в одній відповіді!

        Контекст з бази знань:
        {{context}}
        """

        return unified_prompt

    def vectorize_content(self, file_path):
        logger.info(f"Loading CSV from {file_path}")
        try:
            loader = CSVLoader(file_path)
            pages = loader.load_and_split()
            if not pages:
                logger.error(f"No CSV data found in {file_path}")
                raise ValueError("No CSV data available to create FAISS index")

            text_splitter = CharacterTextSplitter(chunk_size=1600, chunk_overlap=10)
            docs_splitted = text_splitter.split_documents(pages)

            embeddings = OpenAIEmbeddings()
            db = FAISS.from_documents(docs_splitted, embeddings)
            retriever = db.as_retriever(
                search_type="mmr", search_kwargs={'k': 15, 'lambda_mult': 0.25})

            logger.info(f"Successfully loaded CSV and created retriever with {len(docs_splitted)} documents")
            return retriever
        except Exception as e:
            logger.error(f"Error creating retriever from CSV: {e}")
            raise

    def create_simple_rag_chain(self):
        qa_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self.unified_prompt),
                MessagesPlaceholder("chat_history"),
                ("human", "{input}"),
            ]
        )
        question_answer_chain = create_stuff_documents_chain(self.llm, qa_prompt)
        rag_chain = create_retrieval_chain(self.retriever, question_answer_chain)
        return rag_chain

    def create_conversational_rag_chain(self, session_id: str):

        def get_session_history(session_id: str) -> BaseChatMessageHistory:
            try:
                return CustomPostgresChatMessageHistory(
                    self.postgres_table_name,
                    session_id,
                    self.postgres_conn
                )
            except Exception as e:
                logger.warning(f"Failed to create PostgreSQL history for {session_id}: {e}")
                return ChatMessageHistory()

        return RunnableWithMessageHistory(
            self.rag_chain,
            get_session_history,
            input_messages_key="input",
            history_messages_key="chat_history",
            output_messages_key="answer"
        )

    def chat(self, user_prompt: str, session_id: str) -> str:
        logger.info(f"Processing query for session {session_id}: {user_prompt[:100]}...")

        try:
            conversational_chain = self.create_conversational_rag_chain(session_id)

            result = conversational_chain.invoke(
                {"input": user_prompt},
                config={"configurable": {"session_id": session_id}}
            )

            logger.info(f"Successfully processed query for session {session_id} with SINGLE API call")
            return result["answer"]

        except Exception as e:
            logger.error(f"Error processing query for session {session_id}: {e}")
            raise

    def update_prompt(self, new_prompt: str) -> bool:
        if self.prompt_service.update_prompt(new_prompt):
            self.unified_prompt = self.create_unified_prompt()
            self.rag_chain = self.create_simple_rag_chain()
            return True
        return False

    def clear_history(self, session_id: str) -> bool:
        try:
            cursor = self.postgres_conn.cursor()
            cursor.execute(
                f"DELETE FROM {self.postgres_table_name} WHERE session_id = %s",
                (session_id,)
            )
            deleted_count = cursor.rowcount
            self.postgres_conn.commit()
            cursor.close()

            logger.info(f"History cleared: {deleted_count} records for session {session_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to clear history for session_id {session_id}: {e}")
            return False


class OptimizedColabKnowledgeService(KnowledgeService):

    def __init__(self):
        self.prompt_service = PostgresPromptService()
        self.assistant = OptimizedAQPAssistant(settings.CSV_FILE_PATH, self.prompt_service)

    def process_query(self, query: str, session_id: str) -> str:
        return self.assistant.chat(query, session_id)

    def update_prompt(self, new_prompt: str) -> bool:
        return self.assistant.update_prompt(new_prompt)

    def clear_history(self, session_id: str) -> bool:
        return self.assistant.clear_history(session_id)
