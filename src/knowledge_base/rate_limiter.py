import time
import logging
from functools import wraps
import openai
from openai import OpenAI

logger = logging.getLogger(__name__)

class GlobalRateLimiter:

    _instance = None
    _last_request_time = 0
    _request_count = 0
    _start_time = time.time()
    _calls_per_minute = 40
    _min_interval = 60.0 / _calls_per_minute
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            logger.info(f"🚦 Global rate limiter initialized: {cls._calls_per_minute} calls/min (interval: {cls._min_interval:.2f}s)")
        return cls._instance
    
    def wait_if_needed(self):
        current_time = time.time()
        time_since_last = current_time - GlobalRateLimiter._last_request_time
        
        if time_since_last < GlobalRateLimiter._min_interval:
            sleep_time = GlobalRateLimiter._min_interval - time_since_last
            logger.info(f"⏱️ Global rate limit: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        
        GlobalRateLimiter._last_request_time = time.time()
        GlobalRateLimiter._request_count += 1
        
        # Логируем статистику каждые 5 запросов
        if GlobalRateLimiter._request_count % 5 == 0:
            elapsed = time.time() - GlobalRateLimiter._start_time
            rate = GlobalRateLimiter._request_count / (elapsed / 60) if elapsed > 0 else 0
            logger.info(f"📊 Global API Stats: {GlobalRateLimiter._request_count} calls, rate: {rate:.1f}/min")

_global_rate_limiter = GlobalRateLimiter()

_original_chat_completions_create = None
_original_embeddings_create = None

def rate_limited_method(original_method):
    @wraps(original_method)
    def wrapper(*args, **kwargs):
        _global_rate_limiter.wait_if_needed()
        
        try:
            result = original_method(*args, **kwargs)
            logger.debug("✅ OpenAI API call successful")
            return result
        except Exception as e:
            logger.error(f"❌ OpenAI API call failed: {e}")
            raise
    
    return wrapper

def apply_global_rate_limiting():
    global _original_chat_completions_create, _original_embeddings_create
    
    try:
        if _original_chat_completions_create is None:
            _original_chat_completions_create = openai.resources.chat.completions.Completions.create
            openai.resources.chat.completions.Completions.create = rate_limited_method(_original_chat_completions_create)
            logger.info("✅ Patched chat completions with rate limiting")
        
        if _original_embeddings_create is None:
            _original_embeddings_create = openai.resources.embeddings.Embeddings.create
            openai.resources.embeddings.Embeddings.create = rate_limited_method(_original_embeddings_create)
            logger.info("✅ Patched embeddings with rate limiting")
            
        logger.info("🚦 Global OpenAI rate limiting applied successfully!")
        
    except Exception as e:
        logger.error(f"❌ Failed to apply global rate limiting: {e}")

def remove_global_rate_limiting():
    global _original_chat_completions_create, _original_embeddings_create
    
    try:
        if _original_chat_completions_create:
            openai.resources.chat.completions.Completions.create = _original_chat_completions_create
            _original_chat_completions_create = None
        
        if _original_embeddings_create:
            openai.resources.embeddings.Embeddings.create = _original_embeddings_create
            _original_embeddings_create = None
            
        logger.info("🔓 Global rate limiting removed")
        
    except Exception as e:
        logger.error(f"❌ Failed to remove global rate limiting: {e}")

class RateLimitedChatOpenAI:
    """Обертка для обратной совместимости"""
    def __new__(cls, *args, **kwargs):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(*args, **kwargs)