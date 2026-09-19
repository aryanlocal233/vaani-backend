from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Bhashini ULCA API
    BHASHINI_API_KEY: str = ""
    BHASHINI_USER_ID: str = ""
    BHASHINI_BASE_URL: str = "https://dhruva-api.bhashini.gov.in"

    # Optional fallback providers
    AZURE_SPEECH_KEY: str = ""
    AZURE_SPEECH_REGION: str = ""
    GOOGLE_TRANSLATE_KEY: str = ""

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Session / limits
    MAX_SESSIONS: int = 100
    SESSION_TTL_SECONDS: int = 3600

    # Audio
    AUDIO_SAMPLE_RATE: int = 16000
    CHUNK_TIMEOUT_SECONDS: float = 30.0

    # Logging
    LOG_LEVEL: str = "INFO"

    # CORS
    CORS_ALLOW_ORIGINS: str = "*"


@lru_cache
def get_settings() -> Settings:
    return Settings()
