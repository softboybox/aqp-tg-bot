import time
import logging
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

class RateLimitedChatOpenAI(ChatOpenAI):

    _last_request_time = 0
    _request_count = 0
    _start_time = time.time()
    _calls_per_minute = 40
    _min_interval = 60.0 / _calls_per_minute
    
    def __init__(self, calls_per_minute=40, *args, **kwargs):
        RateLimitedChatOpenAI._calls_per_minute = calls_per_minute
        RateLimitedChatOpenAI._min_interval = 60.0 / calls_per_minute
        
        super().__init__(*args, **kwargs)
        
        logger.info(f"🚦 Rate limiter initialized: {calls_per_minute} calls/min (interval: {RateLimitedChatOpenAI._min_interval:.2f}s)")
    
    def _call(self, *args, **kwargs):
        current_time = time.time()
        
        time_since_last = current_time - RateLimitedChatOpenAI._last_request_time
        
        if time_since_last < RateLimitedChatOpenAI._min_interval:
            sleep_time = RateLimitedChatOpenAI._min_interval - time_since_last
            logger.info(f"⏱️ Rate limit: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        
        RateLimitedChatOpenAI._last_request_time = time.time()
        RateLimitedChatOpenAI._request_count += 1
        
        if RateLimitedChatOpenAI._request_count % 10 == 0:
            elapsed = time.time() - RateLimitedChatOpenAI._start_time
            rate = RateLimitedChatOpenAI._request_count / (elapsed / 60)
            logger.info(f"📊 API Stats: {RateLimitedChatOpenAI._request_count} calls, rate: {rate:.1f}/min")
        
        try:
            result = super()._call(*args, **kwargs)
            logger.debug("✅ API call successful")
            return result
        except Exception as e:
            logger.error(f"❌ API call failed: {e}")
            raise