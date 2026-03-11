"""Application configuration backed by environment variables with sensible defaults.

Uses pydantic.BaseSettings so settings can be provided via env or a .env file.
"""
import os
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App server
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000

    # CORS
    ALLOW_ORIGINS: str = "*"

    # Temporary directory for uploaded/processed files
    TEMP_DIR: str = Field(default_factory=lambda: os.path.join(os.getcwd(), "tmp"))

    # Maximum number of rows to include in budget preview
    MAX_PREVIEW_ROWS: int = 200

    # Optionally provide repo root explicitly to locate pipeline modules
    REPO_ROOT: Optional[str] = None

    # Default template path (optional)
    DEFAULT_TEMPLATE_PATH: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
