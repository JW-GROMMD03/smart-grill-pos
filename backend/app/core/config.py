import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD: str | None = os.getenv("REDIS_PASSWORD", None)
    UPSTASH_REDIS_REST_URL: str | None = os.getenv("UPSTASH_REDIS_REST_URL", None)
    UPSTASH_REDIS_REST_TOKEN: str | None = os.getenv("UPSTASH_REDIS_REST_TOKEN", None)
    JWT_SECRET: str = os.getenv("JWT_SECRET", "super-secret-key")
    ADMIN_SECRET_KEY: str = os.getenv("ADMIN_SECRET_KEY", "ADMIN_KEY")
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "*")
    
    # New: SMTP & Vault Config
    SMTP_SERVER: str = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", 465))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    MASTER_KEY: str = os.getenv("MASTER_KEY", "fallback-dev-key")

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )

settings = Settings()