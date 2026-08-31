# app/core/redis.py
import os
import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv()

REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")

# Use from_url to let the library handle the SSL context parsing internally
# By appending ?ssl_cert_reqs=none, it safely disables the problematic validation
redis_url = f"rediss://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}?ssl_cert_reqs=none"

redis_client = aioredis.from_url(redis_url, decode_responses=True)

async def get_redis():
    return redis_client

async def check_redis_connection():
    try:
        await redis_client.ping()
        print("✅ Connected to Upstash Redis successfully!")
    except Exception as e:
        print(f"❌ Failed to connect to Upstash Redis: {e}")