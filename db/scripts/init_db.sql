CREATE TABLE IF NOT EXISTS langchain_chat_history (
    id SERIAL PRIMARY KEY,
    session_id UUID NOT NULL,
    message JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);