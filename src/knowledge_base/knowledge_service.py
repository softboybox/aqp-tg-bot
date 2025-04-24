import os
import logging
import psycopg
from abc import ABC, abstractmethod
from langchain_community.document_loaders import PyPDFLoader, DirectoryLoader
from langchain.text_splitter import CharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.chains import create_history_aware_retriever, create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_postgres import PostgresChatMessageHistory
from src.config.settings import settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class KnowledgeService(ABC):
    @abstractmethod
    def process_query(self, query: str, session_id: str) -> str:
        pass

class AQPAssistant:
    def __init__(self, file_path, system_prompt):
        os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY
        self.retriever = self.vectorize_content(file_path)
        self.llm, self.history_aware_retriever = self.initialize_history_aware_retriever(self.retriever)
        self.rag_chain = self.create_rag_chain(self.llm, self.history_aware_retriever, system_prompt)
        self.conversational_rag_chain = self.create_conversational_rag_chain(self.rag_chain)
        self.postgres_conn = psycopg.connect(settings.LC_DATABASE_URL)
        self.postgres_table_name = settings.LC_CHAT_HISTORY_TABLE_NAME

    def vectorize_content(self, file_path):
        index_path = settings.FAISS_INDEX_PATH
        index_file = os.path.join(index_path, "index.faiss")
        if os.path.exists(index_file):
            logger.info("Loading existing FAISS index from %s", index_path)
            embeddings = OpenAIEmbeddings()
            db = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
        else:
            logger.info("Creating new FAISS index from %s", file_path)
            loader = DirectoryLoader(
                file_path,
                glob="**/*.pdf",
                loader_cls=PyPDFLoader,
                use_multithreading=True,
                show_progress=True,
                silent_errors=True
            )
            pages = loader.load_and_split()
            if not pages:
                logger.error("No PDF files found in %s", file_path)
                raise ValueError("No PDF files available to create FAISS index")
            text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=10)
            docs_splitted = text_splitter.split_documents(pages)
            embeddings = OpenAIEmbeddings()
            db = FAISS.from_documents(docs_splitted, embeddings)
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            db.save_local(index_path)
            logger.info("Saved FAISS index to %s", index_path)
        return db.as_retriever()

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

        llm = ChatOpenAI(model="gpt-4o", max_tokens=700, temperature=0.2)
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

    def create_conversational_rag_chain(self, rag_chain):
        def get_session_history(session_id: str):
            return PostgresChatMessageHistory(
                self.postgres_table_name,
                session_id,
                sync_connection=self.postgres_conn
            )

        conversational_rag_chain = RunnableWithMessageHistory(
            rag_chain,
            get_session_history,
            input_messages_key="input",
            history_messages_key="chat_history",
            output_messages_key="answer"
        )
        return conversational_rag_chain

    def chat(self, user_prompt, session_id):
        result = self.conversational_rag_chain.invoke(
            {"input": user_prompt},
            config={"configurable": {"session_id": session_id}}
        )
        return result["answer"]

class ColabKnowledgeService(KnowledgeService):
    def __init__(self):
        system_prompt = """
        Цей GPT є консультантом з хімії для басейнів і спеціалізується на підборі
        продукції бренду AquaDoctor (https://aquadoctor.ua).
        Він допомагає користувачам визначити необхідні засоби для догляду за басейном
        з урахуванням того, що середній сезон користування триває 3 місяці.
        Відповіді включають точні розрахунки кількості хімії, що базуються на обсягах
        фасування, доступних на сайті виробника, щоб забезпечити оптимальний вибір
        продукції без надлишків або нестачі. GPT перевіряє всі доступні фасування і
        пропонує найоптимальнішу (найвигіднішу та найвідповіднішу) упаковку продукції з
        Aquapolis. Якщо потрібно, GPT комбінує фасування — наприклад, якщо потрібно 6 кг,
        а є фасування 5 кг і 1 кг, то рекомендує їх разом.

        Перед тим, як підібрати хімію, GPT спочатку ставить два основні питання:
        1) "Щоб підібрати потрібну хімію AquaDoctor для вашого басейну, уточніть,
        будь ласка: Розміри басейну (довжина × ширина × глибина або діаметр × глибина,
        якщо басейн круглий)."
        2) "Яка хімія потрібна? Весь набір для догляду впродовж сезону? Чи потрібна хімія
         для чогось конкретного? (шокова обробка, підтримка чистоти, коригування рівня pH,
         альгіцид від водоростей, коагулянт для прозорості тощо)."

        Якщо користувач вказав розмір басейну у форматі "360 на 200", GPT уточнює:
        "Чи правильно я розумію, що у вас круглий басейн?" Якщо басейн не круглий,
        то додатково запитує глибину для точних розрахунків.
        Розрахунок об'єму завжди ведеться з урахуванням того, що рівень води на 20 см
        нижче від загальної висоти басейну. GPT не запитує про наявність системи фільтрації.

        Якщо користувач вибирає набір хімії на сезон (3 місяці), GPT включає у рекомендацію препарати для:
        - Шокової обробки (з розрахунку кожні 2 тижні протягом сезону).
        - Підтримання чистоти води.

        Загальна порада: При температурі вище +35°C: вся хімія має застосовуватись
        частіше або в трохи підвищених дозах, але не більше ніж на 20–25% від стандарту, щоб не перенаситити воду.

        Якщо користувач питає, де купити продукцію або зазначає, що хоче придбати хімію для басейну, GPT рекомендує лише Aquapolis.ua як перевірений магазин.

        Також користувач може отримати консультацію за телефоном 0800300144.
        Твої відповіді не повинні перевищувати 400 слів.

        {context}
        """
        self.assistant = AQPAssistant(settings.PDF_FILES_PATH, system_prompt)

    def process_query(self, query: str, session_id: str) -> str:
        return self.assistant.chat(query, session_id)