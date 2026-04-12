"""
Environment configuration — all secrets from .env
"""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # Supabase
    SUPABASE_URL: str = "https://your-project.supabase.co"
    SUPABASE_KEY: str = "your-anon-key"
    SUPABASE_SERVICE_KEY: str = "your-service-role-key"

    # OpenAI (Whisper)
    OPENAI_API_KEY: str = "sk-your-key"

    # App
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "https://snagflow.vercel.app",
    ]
    MAX_IMAGE_SIZE_MB: int = 10
    SIGNED_URL_EXPIRY: int = 3600  # seconds

    # JWT (Supabase handles this, but for manual verification)
    JWT_SECRET: str = "your-supabase-jwt-secret"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
