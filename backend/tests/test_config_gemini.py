"""Tests that Gemini config settings load correctly from environment."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_gemini_api_key_loads_from_env(monkeypatch):
    """GEMINI_API_KEY should load from environment variable."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-abc123")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    # Force re-creation of settings to pick up monkeypatched env
    from pydantic_settings import BaseSettings
    from app.config import Settings

    fresh_settings = Settings()
    assert fresh_settings.GEMINI_API_KEY == "test-key-abc123"
    assert fresh_settings.GEMINI_MODEL == "gemini-2.5-flash-lite"


def test_gemini_model_has_no_hardcoded_default(monkeypatch):
    """GEMINI_MODEL must NOT have a hardcoded default.

    Hardcoding a model name in source was the bug that caused the 503 storm:
    an earlier default ("gemini-3.1-flash-lite-preview") referenced a model
    that does not exist in Google's API. Deployment policy — which model to
    call — belongs in env, not in code. Tests that need a specific model
    must set it explicitly.
    """
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    from app.config import Settings

    fresh_settings = Settings()
    assert fresh_settings.GEMINI_MODEL == ""


def test_llm_client_fails_fast_when_gemini_model_missing(monkeypatch):
    """call_llm must raise a clear error when GEMINI_MODEL is unset,
    rather than silently calling Google with an empty model name and
    getting a misleading 503."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    # Rebuild the settings singleton so the monkeypatched env takes effect,
    # and reset the llm_client module cache so get_llm_client re-validates.
    import importlib
    from app import config as config_module
    importlib.reload(config_module)
    from app.ai_implementation.pipeline import llm_client as llm_client_module
    importlib.reload(llm_client_module)

    import pytest
    with pytest.raises(RuntimeError, match="GEMINI_MODEL is not set"):
        llm_client_module.get_llm_client()


def test_enterprise_gemini_settings_load_from_env(monkeypatch):
    """Enterprise/Vertex Gemini settings should load from environment."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "budgeting-01")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")

    from app.config import Settings

    fresh_settings = Settings()
    assert fresh_settings.GOOGLE_GENAI_USE_ENTERPRISE is True
    assert fresh_settings.GOOGLE_CLOUD_PROJECT == "budgeting-01"
    assert fresh_settings.GOOGLE_CLOUD_LOCATION == "global"
    assert fresh_settings.GEMINI_MODEL == "gemini-3.5-flash"


def test_enterprise_mode_does_not_require_api_key(monkeypatch):
    """Vertex/ADC mode must not fail because GEMINI_API_KEY is unset."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "budgeting-01")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")

    import importlib
    from app import config as config_module
    importlib.reload(config_module)
    from app.ai_implementation.pipeline import llm_client as llm_client_module
    importlib.reload(llm_client_module)

    class _FakeClient:
        pass

    captured: dict[str, object] = {}

    def _fake_client(**kwargs):
        captured.update(kwargs)
        return _FakeClient()

    monkeypatch.setattr(llm_client_module.genai, "Client", _fake_client)

    client = llm_client_module.get_llm_client()

    assert isinstance(client, _FakeClient)
    assert captured["vertexai"] is True
    assert captured["project"] == "budgeting-01"
    assert captured["location"] == "global"
    assert "api_key" not in captured or captured["api_key"] is None
