from enum import Enum

class BotState(Enum):
    AWAITING_PASSWORD = "awaiting_password"
    AWAITING_PROMPT = "awaiting_prompt"
    COLLECTING_PROMPT = "collecting_prompt"
    AWAITING_CONVERSATION = "awaiting_conversation"
    AWAITING_CSV_UPLOAD = "awaiting_csv_upload"

WAITING_CSV = 1
