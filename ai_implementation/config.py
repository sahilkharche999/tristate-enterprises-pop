"""Configuration for the AI Budget Pipeline using Pydantic BaseSettings."""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    GROQ_API_KEY: str = ""
    DB_PATH: str = str(Path(__file__).parent / "data" / "budget_ai.db")
    MODEL_NAME: str = "llama-3.3-70b-versatile"
    CBR_THRESHOLD: float = 0.95
    CATBOOST_MIN_CASES: int = 100


settings = Settings()
