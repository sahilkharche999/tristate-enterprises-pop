"""Tests that Gemini config settings load correctly from environment."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_gemini_api_key_loads_from_env(monkeypatch):
    """GEMINI_API_KEY should load from environment variable."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-abc123")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")

    # Force re-creation of settings to pick up monkeypatched env
    from pydantic_settings import BaseSettings
    from app.config import Settings

    fresh_settings = Settings()
    assert fresh_settings.GEMINI_API_KEY == "test-key-abc123"
    assert fresh_settings.GEMINI_MODEL == "gemini-3.1-flash-lite-preview"


def test_gemini_model_has_default():
    """GEMINI_MODEL should default to gemini-3.1-flash-lite-preview when not set."""
    from app.config import Settings

    fresh_settings = Settings()
    assert fresh_settings.GEMINI_MODEL == "gemini-3.1-flash-lite-preview"
