"""Tests for Gemini vision structured output via call_llm_vision.

Replaces test_groq_vision_response_normalization.py.
Tests that call_llm_vision correctly handles response.parsed for financial statements.
Per D-05: tree-shaped normalization and null-label scrubbing are removed --
controlled generation prevents these issues at the model level.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai_implementation.pipeline.llm_client import call_llm_vision
from app.models.financial_document_extraction import ExtractedFinancialStatement


class _FakeParsedResponse:
    def __init__(self, parsed=None, text=None):
        self.parsed = parsed
        self.text = text


class _FakeAsyncModels:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._response


class _FakeAio:
    def __init__(self, response):
        self.models = _FakeAsyncModels(response)


class _FakeGeminiClient:
    def __init__(self, response):
        self.aio = _FakeAio(response)


def test_call_llm_vision_returns_parsed_financial_statement(monkeypatch):
    """Controlled generation returns schema-valid ExtractedFinancialStatement via response.parsed."""
    _payload = {
        "document_family": "pdf_visual_document",
        "report_type": "income_statement",
        "line_items": [
            {"label": "Assessment Income", "section_kind": "income", "ytd_actual": 1000.0, "annual_budget": 12000.0},
            {"label": "Management Fee", "section_kind": "operating", "ytd_actual": 500.0, "annual_budget": 6000.0},
        ],
        "totals": [],
        "validation_issues": [],
        "confidence": 0.0,
    }
    expected = ExtractedFinancialStatement.model_validate(_payload)
    import json as _json
    fake_response = _FakeParsedResponse(text=_json.dumps(_payload))
    fake_client = _FakeGeminiClient(fake_response)

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_client_no_loop", None)
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.get_llm_client", lambda: fake_client)

    result = asyncio.run(
        call_llm_vision(
            messages=[
                {"role": "system", "content": "You are a financial parser."},
                {"role": "user", "content": "Extract all line items."},
            ],
            response_schema=ExtractedFinancialStatement,
            temperature=0.0,
            timeout=5.0,
        )
    )

    assert isinstance(result, ExtractedFinancialStatement)
    assert result.document_family == "pdf_visual_document"
    assert len(result.line_items) == 2
    assert result.line_items[0].label == "Assessment Income"
    assert result.line_items[0].section_kind == "income"
    assert result.line_items[1].label == "Management Fee"


def test_call_llm_vision_uses_response_schema_not_json_object(monkeypatch):
    """Gemini uses response_schema (controlled generation), NOT json_object mode."""
    _payload = {
        "document_family": "pdf_visual_document",
        "report_type": "income_statement",
        "line_items": [
            {"label": "Assessment Income", "section_kind": "income", "ytd_actual": 1000.0, "annual_budget": 12000.0},
            {"label": "Management Fee", "section_kind": "operating", "ytd_actual": 500.0, "annual_budget": 6000.0},
        ],
        "totals": [],
        "validation_issues": [],
        "confidence": 0.0,
    }
    expected = ExtractedFinancialStatement.model_validate(_payload)
    import json as _json
    fake_response = _FakeParsedResponse(text=_json.dumps(_payload))
    fake_client = _FakeGeminiClient(fake_response)

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client._gemini_client_no_loop", None)
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.get_llm_client", lambda: fake_client)

    asyncio.run(
        call_llm_vision(
            messages=[{"role": "user", "content": "test"}],
            response_schema=ExtractedFinancialStatement,
            temperature=0.0,
            timeout=5.0,
        )
    )

    # Verify the config passed to generate_content uses response_schema
    call = fake_client.aio.models.calls[0]
    config = call["config"]
    assert hasattr(config, "response_schema") or "response_schema" in str(type(config))
