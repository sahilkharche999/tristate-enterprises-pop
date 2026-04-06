"""Unit tests for the Gemini LLM client wrapper (llm_client.py)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from pydantic import BaseModel
from typing import Optional
from app.ai_implementation.pipeline.llm_client import call_llm, call_llm_vision, get_llm_client


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
    """call_llm should return response.parsed when it is a valid Pydantic instance."""
    expected = SimpleModel(name="test", value=42)
    fake_response = _FakeParsedResponse(parsed=expected)
    fake_client = _FakeGeminiClient(fake_response)

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_client", None)
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

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_client", None)
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

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_client", None)
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
    fake_success_response = _FakeParsedResponse(parsed=expected)

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
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_client", None)
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
    """call_llm_vision should return response.parsed for multimodal requests."""
    expected = SimpleModel(name="vision", value=7)
    fake_response = _FakeParsedResponse(parsed=expected)
    fake_client = _FakeGeminiClient(fake_response)

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_client", None)
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


def test_get_llm_client_creates_singleton(monkeypatch):
    """get_llm_client should create client on first call and reuse on second."""
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_client", None)

    call_count = 0

    class FakeClient:
        pass

    def fake_genai_client(**kwargs):
        nonlocal call_count
        call_count += 1
        return FakeClient()

    import google.genai
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.genai.Client", fake_genai_client)

    client1 = get_llm_client()
    client2 = get_llm_client()

    assert client1 is client2
    assert call_count == 1
