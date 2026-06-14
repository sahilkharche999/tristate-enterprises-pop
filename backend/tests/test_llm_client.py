"""Unit tests for the Gemini LLM client wrapper (llm_client.py)."""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from pydantic import BaseModel
from typing import Optional
from app.ai_implementation.pipeline.llm_client import (
    call_llm,
    call_llm_vision,
    get_llm_client,
)


class SimpleModel(BaseModel):
    name: str
    value: int


class _FakeParsedResponse:
    """Mimics genai GenerateContentResponse with .parsed and .text."""
    def __init__(self, parsed=None, text=None):
        self.parsed = parsed
        self.text = text


class _FakeAsyncModels:
    def __init__(self, response):
        self._response = response

    async def generate_content(self, *, model, contents, config):
        return self._response


class _FakeAio:
    def __init__(self, response):
        self.models = _FakeAsyncModels(response)


class _FakeGeminiClient:
    def __init__(self, response):
        self.aio = _FakeAio(response)


def test_call_llm_returns_parsed_model(monkeypatch):
    """call_llm should prefer response.parsed when the SDK provides it."""
    fake_response = _FakeParsedResponse(
        parsed={"name": "test", "value": 42},
        text='{"name": "wrong", "value": 0}',
    )
    fake_client = _FakeGeminiClient(fake_response)

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_client_no_loop", None)
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.get_llm_client", lambda: fake_client)

    result = asyncio.run(
        call_llm(
            messages=[{"role": "user", "content": "test prompt"}],
            response_schema=SimpleModel,
            temperature=0.0,
            timeout=5.0,
        )
    )

    assert isinstance(result, SimpleModel)
    assert result.name == "test"
    assert result.value == 42


def test_call_llm_falls_back_to_text_parsing(monkeypatch):
    """call_llm should fall back to model_validate_json(response.text) when parsed is None."""
    fake_response = _FakeParsedResponse(parsed=None, text='{"name": "fallback", "value": 99}')
    fake_client = _FakeGeminiClient(fake_response)

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_client_no_loop", None)
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.get_llm_client", lambda: fake_client)

    result = asyncio.run(
        call_llm(
            messages=[{"role": "user", "content": "test"}],
            response_schema=SimpleModel,
            temperature=0.0,
            timeout=5.0,
        )
    )

    assert isinstance(result, SimpleModel)
    assert result.name == "fallback"
    assert result.value == 99


def test_call_llm_returns_none_on_empty_response(monkeypatch):
    """call_llm should return None when response has no parsed result and no text."""
    fake_response = _FakeParsedResponse(parsed=None, text=None)
    fake_client = _FakeGeminiClient(fake_response)

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_client_no_loop", None)
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.get_llm_client", lambda: fake_client)

    result = asyncio.run(
        call_llm(
            messages=[{"role": "user", "content": "test"}],
            response_schema=SimpleModel,
            timeout=5.0,
        )
    )

    assert result is None


def test_call_llm_retries_on_rate_limit(monkeypatch):
    """call_llm should retry on 429 rate-limit errors with exponential backoff, then succeed."""
    from google.genai import errors as genai_errors

    expected = SimpleModel(name="retry_success", value=200)
    fake_success_response = _FakeParsedResponse(
        text='{"name": "retry_success", "value": 200}',
    )

    call_count = 0

    class _RateLimitThenSuccessModels:
        async def generate_content(self, *, model, contents, config):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First attempt: simulate 429 rate limit
                # ClientError(code: int, response_json: Any)
                raise genai_errors.ClientError(
                    429,
                    {"error": {"code": 429, "message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED"}},
                )
            # Second attempt: succeed
            return fake_success_response

    class _FakeAioRetry:
        def __init__(self):
            self.models = _RateLimitThenSuccessModels()

    class _FakeRetryClient:
        def __init__(self):
            self.aio = _FakeAioRetry()

    fake_client = _FakeRetryClient()
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_client_no_loop", None)
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.get_llm_client", lambda: fake_client)

    # Patch asyncio.sleep to avoid real delays and to verify backoff was called
    sleep_calls = []

    async def mock_sleep(seconds):
        sleep_calls.append(seconds)
        # Don't actually sleep

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.asyncio.sleep", mock_sleep)

    result = asyncio.run(
        call_llm(
            messages=[{"role": "user", "content": "test"}],
            response_schema=SimpleModel,
            temperature=0.0,
            timeout=5.0,
        )
    )

    assert isinstance(result, SimpleModel)
    assert result.name == "retry_success"
    assert result.value == 200
    assert call_count == 2, f"Expected 2 API calls (1 fail + 1 success), got {call_count}"
    # Verify backoff delay was applied (2 seconds for first retry per backoff_delays=[2,4,8])
    assert any(d == 2 for d in sleep_calls), f"Expected 2s backoff delay, got sleep calls: {sleep_calls}"


def test_call_llm_vision_returns_parsed_model(monkeypatch):
    """call_llm_vision should return a Pydantic instance parsed from response.text
    for multimodal requests (same JSON-text path as call_llm).
    """
    fake_response = _FakeParsedResponse(text='{"name": "vision", "value": 7}')
    fake_client = _FakeGeminiClient(fake_response)

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_client_no_loop", None)
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.get_llm_client", lambda: fake_client)

    result = asyncio.run(
        call_llm_vision(
            messages=[
                {"role": "system", "content": "You are a parser."},
                {"role": "user", "content": [
                    {"type": "text", "text": "document text here"},
                    {"type": "image", "data": b"\x89PNG fake", "mime_type": "image/png"},
                ]},
            ],
            response_schema=SimpleModel,
            temperature=0.0,
            timeout=5.0,
        )
    )

    assert isinstance(result, SimpleModel)
    assert result.name == "vision"
    assert result.value == 7


def test_call_llm_vision_timeout_log_includes_request_context(monkeypatch, caplog):
    """Timeout logs should identify which vision job hung."""

    class _TimeoutModels:
        async def generate_content(self, *, model, contents, config):
            raise asyncio.TimeoutError()

    class _TimeoutAio:
        def __init__(self):
            self.models = _TimeoutModels()

    class _TimeoutClient:
        def __init__(self):
            self.aio = _TimeoutAio()

    async def _no_sleep(seconds):
        return None

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.get_llm_client", lambda: _TimeoutClient())
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.asyncio.sleep", _no_sleep)
    caplog.set_level(logging.WARNING, logger="app.ai_implementation.pipeline.llm_client")

    result = asyncio.run(
        call_llm_vision(
            messages=[
                {"role": "system", "content": "You are a parser."},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Filename: Old Mill Final 2024.pdf\nPage: 3\nImage for Page: 7",
                        },
                        {"type": "image", "data": b"abc", "mime_type": "image/png"},
                        {"type": "image", "data": b"defg", "mime_type": "image/png"},
                    ],
                },
            ],
            response_schema=SimpleModel,
            temperature=0.0,
            timeout=5.0,
        )
    )

    assert result is None
    assert "purpose=SimpleModel" in caplog.text
    assert "schema=SimpleModel" in caplog.text
    assert "filename=Old Mill Final 2024.pdf" in caplog.text
    assert "pages=3,7" in caplog.text
    assert "image_count=2" in caplog.text
    assert "image_bytes=7" in caplog.text


def test_get_llm_client_caches_within_same_loop(monkeypatch):
    """get_llm_client should reuse a cached client for the same event loop.

    This is the happy path: a FastAPI request handler that makes multiple
    Gemini calls on the same loop should share one client, not allocate
    a new one each time.
    """
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_clients", {})
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")

    # Re-read settings so _require_gemini_config passes
    import importlib
    from app import config as config_module
    importlib.reload(config_module)
    from app.ai_implementation.pipeline import llm_client as llm_client_module
    importlib.reload(llm_client_module)
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_clients", {})

    call_count = 0

    class FakeClient:
        pass

    def fake_genai_client(**kwargs):
        nonlocal call_count
        call_count += 1
        return FakeClient()

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.genai.Client", fake_genai_client)

    async def _reuse_twice():
        c1 = llm_client_module.get_llm_client()
        c2 = llm_client_module.get_llm_client()
        return c1, c2

    client1, client2 = asyncio.run(_reuse_twice())
    assert client1 is client2
    assert call_count == 1


def test_get_llm_client_does_not_share_across_event_loops(monkeypatch):
    """get_llm_client must NOT share a cached client across different event loops.

    Regression guard for the 'Event loop is closed' bug: when
    ThreadPoolExecutor-based column detection ran on its own short-lived loop
    and populated the module singleton, the FastAPI main loop later reused
    the same client — whose internal aiohttp session was bound to the now-
    dead thread loop — and the first parallel page extraction failed with
    'Event loop is closed'. The fix is to cache per event loop (keyed on
    id(loop)) so each loop sees its own fresh client.
    """
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_clients", {})
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")

    import importlib
    from app import config as config_module
    importlib.reload(config_module)
    from app.ai_implementation.pipeline import llm_client as llm_client_module
    importlib.reload(llm_client_module)
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_clients", {})

    call_count = 0

    class FakeClient:
        pass

    def fake_genai_client(**kwargs):
        nonlocal call_count
        call_count += 1
        return FakeClient()

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.genai.Client", fake_genai_client)

    async def _get_on_this_loop():
        return llm_client_module.get_llm_client()

    # Loop A (simulates column detection's thread-pool loop)
    client_loop_a = asyncio.run(_get_on_this_loop())
    # Loop B (simulates FastAPI request handler's main loop)
    client_loop_b = asyncio.run(_get_on_this_loop())

    # Each asyncio.run() creates a fresh loop. The fix guarantees each loop
    # gets its own client — otherwise reusing client_loop_a on loop B would
    # reproduce the 'Event loop is closed' error.
    assert client_loop_a is not client_loop_b, (
        "Clients were shared across event loops — would cause 'Event loop is closed' "
        "on the second loop if the first loop closed between calls."
    )
    assert call_count == 2


def test_get_llm_client_uses_vertex_mode_when_enterprise_enabled(monkeypatch):
    """Enterprise mode should create a Vertex/ADC Gemini client."""
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

    llm_client_module._gemini_client_no_loop = None
    llm_client_module._gemini_clients.clear()

    captured: dict[str, object] = {}

    class FakeClient:
        pass

    def fake_genai_client(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.genai.Client", fake_genai_client)

    client = llm_client_module.get_llm_client()

    assert isinstance(client, FakeClient)
    assert captured["vertexai"] is True
    assert captured["project"] == "budgeting-01"
    assert captured["location"] == "global"


def test_get_llm_client_requires_api_key_when_enterprise_disabled(monkeypatch):
    """Local/API-key mode should still fail fast without GEMINI_API_KEY."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "false")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")

    import importlib
    from app import config as config_module
    importlib.reload(config_module)
    from app.ai_implementation.pipeline import llm_client as llm_client_module
    importlib.reload(llm_client_module)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not set"):
        llm_client_module.get_llm_client()
