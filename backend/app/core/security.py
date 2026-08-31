# app/core/security.py
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import bcrypt  # <-- Using raw bcrypt instead of passlib
from datetime import datetime, timezone, timedelta
import redis.exceptions
from app.core.config import settings
from app.core.redis import redis_client  # Use centralized, SSL-enabled client

security = HTTPBearer()

class SecurityEngine:
    @staticmethod
    def hash_password(password: str) -> str:
        """Hashes a plaintext PIN/password using raw bcrypt."""
        # bcrypt requires bytes, so we encode the string
        pwd_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt()
        hashed_bytes = bcrypt.hashpw(pwd_bytes, salt)
        # return as a standard string for database storage
        return hashed_bytes.decode('utf-8')

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verifies a plaintext PIN/password against a bcrypt hash."""
        pwd_bytes = plain_password.encode('utf-8')
        hash_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(pwd_bytes, hash_bytes)

    @staticmethod
    async def check_rate_limit(ip_or_user: str) -> None:
        lockout_key = f"login_lockout:{ip_or_user}"
        try:
            is_locked = await redis_client.get(lockout_key)
            if is_locked:
                ttl = await redis_client.ttl(lockout_key)
                minutes = max(1, ttl // 60)
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many failed attempts. Account locked. Try again in {minutes} minutes."
                )
        except (redis.exceptions.ConnectionError, redis.exceptions.RedisError):
            pass  # Fallback gracefully to avoid 500 crashes if Redis drops

    @staticmethod
    async def record_failed_attempt(ip_or_user: str) -> None:
        attempts_key = f"login_attempts:{ip_or_user}"
        lockout_key = f"login_lockout:{ip_or_user}"
        try:
            attempts = await redis_client.incr(attempts_key)
            
            # If it's the first failed attempt, set the strike counter to expire in 1 hour
            if attempts == 1:
                await redis_client.expire(attempts_key, 3600)
                
            # TRIGGER LOCKOUT: Exactly on the 3rd failed attempt
            if attempts >= 3:
                # Lock out for 1 hour (3600 seconds)
                await redis_client.setex(lockout_key, 3600, "locked")
                # Wipe strikes clean so they start fresh after lockout ends
                await redis_client.delete(attempts_key)
                
        except (redis.exceptions.ConnectionError, redis.exceptions.RedisError):
            pass

    @staticmethod
    async def reset_attempts(ip_or_user: str) -> None:
        try:
            await redis_client.delete(f"login_attempts:{ip_or_user}")
            await redis_client.delete(f"login_lockout:{ip_or_user}")
        except (redis.exceptions.ConnectionError, redis.exceptions.RedisError):
            pass

    @staticmethod
    def create_access_token(data: dict) -> str:
        payload = data.copy()
        # Use timezone-aware UTC datetime (datetime.utcnow() is deprecated)
        payload.update({"exp": datetime.now(timezone.utc) + timedelta(hours=12)})
        return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")

    @staticmethod
    def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> dict:
        try:
            payload = jwt.decode(
                credentials.credentials, 
                settings.JWT_SECRET, 
                algorithms=["HS256"]
            )
            return payload
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Session expired.")
        except jwt.PyJWTError:
            raise HTTPException(status_code=401, detail="Invalid auth token.")