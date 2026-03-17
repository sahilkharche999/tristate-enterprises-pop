"""Application configuration backed by environment variables with sensible defaults.

Uses pydantic.BaseSettings so settings can be provided via env or a .env file.
"""
import os
import secrets
import logging
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_INSECURE_DEFAULT = "CHANGE-ME-IN-PRODUCTION"


class Settings(BaseSettings):
    # App server
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000

    # CORS (must list specific origins for credentials: 'include' to work)
    ALLOW_ORIGINS: str = "http://localhost,http://localhost:80,http://localhost:5173,http://0.0.0.0,http://0.0.0.0:80"

    # Temporary directory for uploaded/processed files
    TEMP_DIR: str = Field(default_factory=lambda: os.path.join(os.getcwd(), "tmp"))

    # Maximum number of rows to include in budget preview
    MAX_PREVIEW_ROWS: int = 200

    # Optionally provide repo root explicitly to locate pipeline modules
    REPO_ROOT: Optional[str] = None

    # Default template path (optional)
    DEFAULT_TEMPLATE_PATH: Optional[str] = None

    # JWT Authentication
    JWT_SECRET_KEY: str = _INSECURE_DEFAULT
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Cookie security (set True in production with HTTPS)
    COOKIE_SECURE: bool = False

    # AI Pipeline
    GROQ_API_KEY: str = ""
    DB_PATH: str = str(Path(__file__).parent / "ai_implementation" / "data" / "budget_ai.db")
    MODEL_NAME: str = "llama-3.3-70b-versatile"
    CBR_THRESHOLD: float = 0.95
    CATBOOST_ENABLED: bool = False  # Set True to enable CatBoost ML stage
    CATBOOST_MIN_CASES: int = 100

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()

# Fail-safe: refuse to run with the insecure default secret
if settings.JWT_SECRET_KEY == _INSECURE_DEFAULT:
    _generated = secrets.token_hex(32)
    logger.warning(
        "JWT_SECRET_KEY not set! Generated a random key for this session. "
        "Sessions will NOT survive restarts. Set JWT_SECRET_KEY in your .env file."
    )
    settings.JWT_SECRET_KEY = _generated
