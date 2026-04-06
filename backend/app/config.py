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
_BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    # App server
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000

    # CORS (must list specific origins for credentials: 'include' to work)
    ALLOW_ORIGINS: str = "http://localhost,http://localhost:80,http://localhost:5173,http://0.0.0.0,http://0.0.0.0:80"

    # Temporary directory for uploaded/processed files
    TEMP_DIR: str = Field(default_factory=lambda: os.path.join(os.getcwd(), "tmp"))

    # Managed retained-file root for durable budget history artifacts
    BUDGET_STORAGE_ROOT: str = Field(
        default_factory=lambda: str(_BACKEND_ROOT / "data" / "budget-storage")
    )

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

    # Cookie security (True for HTTPS/production, set False for local dev)
    COOKIE_SECURE: bool = True

    # AI Pipeline (Gemini)
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.1-flash-lite-preview"
    DB_PATH: str = str(Path(__file__).parent / "ai_implementation" / "data" / "budget_ai.db")
    DOCUMENT_VLM_ENABLED: bool = False
    DOCUMENT_VLM_MAX_PAGES: int = 6
    DOCUMENT_VLM_MAX_RETRIES: int = 1
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
