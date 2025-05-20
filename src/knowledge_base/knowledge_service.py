import os
import logging
import psycopg
from abc import ABC, abstractmethod
from langchain_community.document_loaders import CSVLoader
from langchain.text_splitter import CharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.chains import create_history_aware_retriever, create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_postgres import PostgresChatMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from src.prompt.prompt_service import PromptService, PostgresPromptService
from src.config.settings import settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EmptyRetriever(BaseRetriever):
    def _get_relevant_documents(self, query, *, run_manager=None, **kwargs):
        return []

    async def _aget_relevant_documents(self, query, *, run_manager=None, **kwargs):
        return []

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

class AQPAssistant:
    def __init__(self, file_path, prompt_service: PromptService):
        os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY
        self.retriever = self.vectorize_content(file_path)
        self.empty_retriever = EmptyRetriever()
        
        self.prompt_service = prompt_service
        system_prompt = self.prompt_service.get_current_prompt()
        self.products_prompt = settings.PRODUCTS_PROMPT
        self.dosage_prompt = settings.DOSAGE_PROMPT
        
        self.llm, self.history_aware_retriever = self.initialize_history_aware_retriever(self.retriever)
        _, self.history_aware_retriever_limited = self.initialize_history_aware_retriever(self.empty_retriever)
        
        self.rag_chain_products = self.create_rag_chain(self.llm, self.history_aware_retriever, self.products_prompt)
        self.product_rag_chain = self.create_conversational_rag_chain(self.rag_chain_products, "products_session", shared=False)
        
        self.rag_chain_dosage = self.create_rag_chain(self.llm, self.history_aware_retriever, self.dosage_prompt)
        self.dosage_rag_chain = self.create_conversational_rag_chain(self.rag_chain_dosage, "dosage_session", shared=False)
        
        self.rag_chain_final_no_rag = self.create_rag_chain(self.llm, self.history_aware_retriever_limited, system_prompt)
        self.final_answer_chain_no_rag = self.create_conversational_rag_chain(self.rag_chain_final_no_rag, "final_answer_chain", shared=True)
        
        self.rag_chain_final = self.create_rag_chain(self.llm, self.history_aware_retriever, system_prompt)
        self.final_answer_chain = self.create_conversational_rag_chain(self.rag_chain_final, "final_answer_chain", shared=True)
        
        self.postgres_conn = psycopg.connect(settings.LC_DATABASE_URL)
        self.postgres_table_name = settings.LC_CHAT_HISTORY_TABLE_NAME
        
        # Инициализируем _final_store, если его еще нет
        if not hasattr(self, "_final_store"):
            self._final_store = {}

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
                search_type="mmr", search_kwargs={'k': 10, 'lambda_mult': 0.25})
            
            logger.info(f"Successfully loaded CSV and created retriever with {len(docs_splitted)} documents")
            return retriever
        except Exception as e:
            logger.error(f"Error creating retriever from CSV: {e}")
            raise

    def initialize_history_aware_retriever(self, retriever):
        contextualize_q_system_prompt = (
            "Given a chat history and the latest user question "
            "which might reference context in the chat history, "
            "formulate a standalone question which can be understood "
            "without the chat history. Do NOT answer the question, "
            "just reformulate it if needed and otherwise return it as is."
        )

        contextualize_q_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", contextualize_q_system_prompt),
                MessagesPlaceholder("chat_history"),
                ("human", "{input}"),
            ]
        )

        llm = ChatOpenAI(model="chatgpt-4o-latest", temperature=0)
        history_aware_retriever = create_history_aware_retriever(
            llm, retriever, contextualize_q_prompt
        )
        return llm, history_aware_retriever

    def create_rag_chain(self, llm, history_aware_retriever, system_prompt):
        qa_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                MessagesPlaceholder("chat_history"),
                ("human", "{input}"),
            ]
        )
        question_answer_chain = create_stuff_documents_chain(llm, qa_prompt)
        rag_chain = create_retrieval_chain(history_aware_retriever, question_answer_chain)
        return rag_chain

    def create_conversational_rag_chain(self, rag_chain, session_id, shared=False):
        if shared:
            if not hasattr(self, "_final_store"):
                self._final_store = {}

            def get_session_history(session_id: str):
                if session_id not in self._final_store:
                    self._final_store[session_id] = ChatMessageHistory()
                return self._final_store[session_id]
        else:
            store = {}

            def get_session_history(session_id: str):
                if session_id not in store:
                    store[session_id] = ChatMessageHistory()
                return store[session_id]

        return RunnableWithMessageHistory(
            rag_chain,
            get_session_history,
            input_messages_key="input",
            history_messages_key="chat_history",
            output_messages_key="answer"
        )

    def chat(self, user_prompt, session_id):
        logger.info(f"Processing query for session {session_id}: {user_prompt[:100]}...")
        
        # 1. Сначала получаем список продуктов
        result1 = self.product_rag_chain.invoke(
            {"input": user_prompt},
            config={"configurable": {"session_id": "products_session"}}
        )

        # Если это общий вопрос (не о продуктах), используем основную цепочку
        if result1["answer"] == "0":
            logger.info("General question detected, using final answer chain")
            result = self.final_answer_chain.invoke(
                {"input": user_prompt},
                config={"configurable": {"session_id": session_id}}
            )
            return result["answer"]

        product_names = [line.strip() for line in result1["answer"].split("\n") if line.strip()]
        logger.info(f"Products identified: {product_names}")
        
        dosage_results = []
        for product_name in product_names:
            result = self.dosage_rag_chain.invoke(
                {"input": product_name},
                config={"configurable": {"session_id": "dosage_session"}}
            )
            dosage_results.append(f"{product_name}\n{result['answer']}")

        final_input = user_prompt.strip() + "\n\n" + "\n\n".join(dosage_results)
        logger.info(f"Generating final answer with info about {len(dosage_results)} products")
        
        final_answer = self.final_answer_chain_no_rag.invoke(
            {"input": final_input},
            config={"configurable": {"session_id": session_id}}
        )
        
        return final_answer["answer"]

    def update_prompt(self, new_prompt: str) -> bool:
        if self.prompt_service.update_prompt(new_prompt):
            system_prompt = self.prompt_service.get_current_prompt()
            
            self.rag_chain_final_no_rag = self.create_rag_chain(self.llm, self.history_aware_retriever_limited, system_prompt)
            self.final_answer_chain_no_rag = self.create_conversational_rag_chain(self.rag_chain_final_no_rag, "final_answer_chain", shared=True)
            
            self.rag_chain_final = self.create_rag_chain(self.llm, self.history_aware_retriever, system_prompt)
            self.final_answer_chain = self.create_conversational_rag_chain(self.rag_chain_final, "final_answer_chain", shared=True)
            
            return True
        return False
        
    def clear_history(self, session_id: str) -> bool:
        try:
            cursor = self.postgres_conn.cursor()
            cursor.execute(
                "DELETE FROM langchain_chat_history WHERE session_id = %s",
                (session_id,)
            )
            deleted_rows = cursor.rowcount
            self.postgres_conn.commit()
            cursor.close()
            
            if hasattr(self, "_final_store"):
                if session_id in self._final_store:
                    self._final_store[session_id] = ChatMessageHistory()
                    logger.info(f"In-memory history cleared for session_id {session_id}")
                
                if "final_answer_chain" in self._final_store:
                    self._final_store["final_answer_chain"] = ChatMessageHistory()
                    logger.info("In-memory history cleared for final_answer_chain")
            
            logger.info(f"History cleared from database: {deleted_rows} rows")
            return True
        except Exception as e:
            logger.error(f"Failed to clear history for session_id {session_id}: {e}")
            return False

class ColabKnowledgeService(KnowledgeService):
    def __init__(self):
        self.prompt_service = PostgresPromptService()
        self.assistant = AQPAssistant(settings.CSV_FILE_PATH, self.prompt_service)

    def process_query(self, query: str, session_id: str) -> str:
        return self.assistant.chat(query, session_id)

    def update_prompt(self, new_prompt: str) -> bool:
        return self.assistant.update_prompt(new_prompt)
        
    def clear_history(self, session_id: str) -> bool:
        return self.assistant.clear_history(session_id)