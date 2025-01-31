from collections import defaultdict
import time
import asyncio
from googleapiclient.errors import HttpError

class YouTubeRateLimiter:
    def __init__(self):
        self.requests = []
        self.QUOTA_PER_DAY = 10000
        self.REQUESTS_PER_MINUTE = 50
        self.current_quota = 0
        
    async def wait_if_needed(self):
        current_time = time.time()
        # Remove requests older than 1 minute
        self.requests = [req_time for req_time in self.requests 
                        if current_time - req_time < 60]
        
        if len(self.requests) >= self.REQUESTS_PER_MINUTE:
            sleep_time = 60 - (current_time - self.requests[0])
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
                
        self.requests.append(current_time)

class YouTubeCache:
    def __init__(self, cache_duration=3600):  # Cache for 1 hour
        self.cache = {}
        self.cache_duration = cache_duration
        self.channel_subscribers = defaultdict(set)  # Track guilds subscribed to each channel
        
    def add_channel_subscriber(self, channel_id, guild_id):
        self.channel_subscribers[channel_id].add(guild_id)
        
    def remove_channel_subscriber(self, channel_id, guild_id):
        if guild_id in self.channel_subscribers[channel_id]:
            self.channel_subscribers[channel_id].remove(guild_id)
            if not self.channel_subscribers[channel_id]:
                del self.channel_subscribers[channel_id]
                
    def get_subscriber_guilds(self, channel_id):
        return self.channel_subscribers[channel_id]

    def get(self, key):
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.cache_duration:
                return data
            del self.cache[key]
        return None

    def set(self, key, value):
        self.cache[key] = (value, time.time())

def safe_api_call(method, **kwargs):
    try:
        return method.execute()
    except HttpError as e:
        if e.resp.status == 403:
            raise Exception("YouTube API quota exceeded")
        elif e.resp.status == 404:
            raise Exception("YouTube channel not found")
        else:
            raise Exception(f"YouTube API error: {str(e)}")
    except Exception as e:
        raise Exception(f"Unexpected error: {str(e)}")
