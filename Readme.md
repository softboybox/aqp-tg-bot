# AQP Telegram Bot

Телеграм-бот для рекомендаций химии для бассейнов AquaDoctor. 

## Установка

### Требования
- Docker и Docker Compose
- Python 3.10 (для локальной разработки)
- Доступ к Google Drive для скачивания PDF-файлов

### Шаги установки

1. **Клонируйте репозиторий**:
   ```bash
   git clone https://github.com/softboybox/aqp-tg-bot.git
   cd aqp-tg-bot

2. **Создайте папку pdf_files**:
   ```bash
   mkdir pdf_files

3. **Скачайте PDF-файлы базы знаний:**
   - Перейдите по ссылке: [Google Drive](https://drive.google.com/drive/folders/1OiHewQRQyq3mqxeSOxQb3hCqfABFWfVd).
   - Скачайте все PDF-файлы и поместите их в папку `./pdf_files`.

   
4. **Создайте файл .env**:

   В корне проекта создайте файл .env со следующими переменными:

   ```bash
   TELEGRAM_TOKEN=your_telegram_token
   OPENAI_API_KEY=your_openai_api_key
   DB_HOST=db
   DB_PORT=5432
   DB_NAME=knowledge_bot
   DB_USER=bot_user
   LC_DATABASE_URL=postgresql://bot_user:password@db:5432/knowledge_bot
   PDF_FILES_PATH=/pdf_files
   FAISS_INDEX_PATH=/app/faiss_index
   LC_CHAT_HISTORY_TABLE_NAME=langchain_chat_history
   ADMIN_PASSWORD=your_admin_password

        
5. **Запустите приложение:**
   ```bash
   docker-compose up -d

6. **После команды ниже в боте можно пройти авторизацию:**
   ```bash
   /login