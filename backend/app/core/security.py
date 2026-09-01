# app/core/security.py
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import bcrypt  
from datetime import datetime, timezone, timedelta
import redis.exceptions
from app.core.config import settings
from app.core.redis import redis_client  
from app.core.supabase import supabase

security = HTTPBearer()

class SecurityEngine:
    @staticmethod
    def hash_password(password: str) -> str:
        """Hashes a plaintext PIN/password using raw bcrypt."""
        pwd_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt()
        hashed_bytes = bcrypt.hashpw(pwd_bytes, salt)
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
            pass

    @staticmethod
    async def record_failed_attempt(ip_or_user: str) -> None:
        attempts_key = f"login_attempts:{ip_or_user}"
        lockout_key = f"login_lockout:{ip_or_user}"
        try:
            attempts = await redis_client.incr(attempts_key)
            if attempts == 1:
                await redis_client.expire(attempts_key, 3600)
            if attempts >= 3:
                await redis_client.setex(lockout_key, 3600, "locked")
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

    @staticmethod
    async def log_event(event_type: str, user_id: str, username: str, description: str) -> None:
        """Logs security and administrative audit events into Supabase."""
        try:
            supabase.table("audit_logs").insert({
                "event_type": event_type,
                "user_id": user_id,
                "username": username,
                "description": description,
                "created_at": datetime.now(timezone.utc).isoformat()
            }).execute()
        except Exception:
            pass