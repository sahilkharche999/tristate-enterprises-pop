"""Stage 1 pilot tests — ``build_classify_callback`` with ``response_schema``.

These tests mock the ``google-genai`` client so the unit suite never
makes a real Gemini call. They confirm the callback:

* attaches ``response_schema=WirePageInventoryBatch`` to the SDK call
* consumes the SDK's typed ``response.parsed`` instance directly
* converts wire ``Optional[float]`` / ``Optional[str]`` ``None`` values
  back to the domain model's defaults (``0.0`` and ``""``)
* returns ``[]`` when the SDK fails to parse (``response.parsed is None``)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.dre_extraction.gemini_callbacks import (
    build_classify_callback,
    build_extract_callback,
)
from app.dre_extraction.page_classification import PageBatch
from app.dre_extraction.schemas import PageInventoryEntry
from app.dre_extraction.wire_schemas import (
    WirePageInventoryBatch,
    WirePageInventoryEntry,
)


@dataclass
class _StubRendered:
    page_number: int
    content: bytes


class _StubResponse:
    def __init__(self, parsed: Any, text: str = "") -> None:
        self.parsed = parsed
        self.text = text


class _StubModels:
    def __init__(self, parsed: Any) -> None:
        self._parsed = parsed
        self.last_config: Any = None
        self.last_contents: Any = None

    def generate_content(self, *, model: str, contents: Any, config: Any) -> _StubResponse:
        self.last_config = config
        self.last_contents = contents
        return _StubResponse(self._parsed)


class _StubClient:
    def __init__(self, parsed: Any) -> None:
        self.models = _StubModels(parsed)


def _rendered_pages(page_numbers: list[int]) -> dict[int, _StubRendered]:
    return {n: _StubRendered(page_number=n, content=b"\x89PNG_stub") for n in page_numbers}


class TestClassifyCallbackStructuredOutput:
    def test_attaches_response_schema_to_generate_content_config(self) -> None:
        """The pilot callback MUST send ``response_schema=WirePageInventoryBatch``."""
        parsed = WirePageInventoryBatch(page_inventory=[])
        client = _StubClient(parsed=parsed)
        callback = build_classify_callback(
            client,
            model="gemini-flash-latest",
            rendered_pages_by_num=_rendered_pages([1]),
        )
        callback(PageBatch(page_numbers=[1]))
        assert client.models.last_config.response_schema is WirePageInventoryBatch
        assert client.models.last_config.response_mime_type == "application/json"
        assert client.models.last_config.temperature == 0.0

    def test_consumes_response_parsed_directly(self) -> None:
        """When ``response.parsed`` is a typed wire model, convert to domain."""
        parsed = WirePageInventoryBatch(
            page_inventory=[
                WirePageInventoryEntry(
                    page_number=1, page_type="cover", confidence=0.95, notes="title page"
                ),
                WirePageInventoryEntry(
                    page_number=2,
                    page_type="unit_summary",
                    confidence=0.87,
                    notes="unit table on lower half",
                ),
            ]
        )
        client = _StubClient(parsed=parsed)
        callback = build_classify_callback(
            client,
            model="gemini-flash-latest",
            rendered_pages_by_num=_rendered_pages([1, 2]),
        )
        out = callback(PageBatch(page_numbers=[1, 2]))
        assert len(out) == 2
        assert all(isinstance(e, PageInventoryEntry) for e in out)
        assert out[0].page_number == 1
        assert out[0].page_type == "cover"
        assert out[0].confidence == pytest.approx(0.95)
        assert out[0].notes == "title page"
        assert out[1].page_type == "unit_summary"

    def test_none_optional_fields_fall_back_to_domain_defaults(self) -> None:
        """Wire ``confidence=None`` / ``notes=None`` MUST become ``0.0`` / ``""``."""
        parsed = WirePageInventoryBatch(
            page_inventory=[
                WirePageInventoryEntry(
                    page_number=3, page_type="proration_schedule", confidence=None, notes=None
                ),
            ]
        )
        client = _StubClient(parsed=parsed)
        callback = build_classify_callback(
            client,
            model="gemini-flash-latest",
            rendered_pages_by_num=_rendered_pages([3]),
        )
        out = callback(PageBatch(page_numbers=[3]))
        assert len(out) == 1
        entry = out[0]
        assert isinstance(entry, PageInventoryEntry)
        assert entry.page_number == 3
        assert entry.page_type == "proration_schedule"
        assert entry.confidence == 0.0  # domain default kicks in
        assert entry.notes == ""  # domain default kicks in

    def test_returns_empty_list_when_response_parsed_is_none(self) -> None:
        """If the SDK can't parse Gemini's output, fall back to ``[]``."""
        client = _StubClient(parsed=None)
        callback = build_classify_callback(
            client,
            model="gemini-flash-latest",
            rendered_pages_by_num=_rendered_pages([1]),
        )
        out = callback(PageBatch(page_numbers=[1]))
        assert out == []

    def test_returns_empty_list_when_inventory_is_empty(self) -> None:
        """Empty inventory survives the round-trip as ``[]``."""
        parsed = WirePageInventoryBatch(page_inventory=[])
        client = _StubClient(parsed=parsed)
        callback = build_classify_callback(
            client,
            model="gemini-flash-latest",
            rendered_pages_by_num=_rendered_pages([1, 2, 3]),
        )
        out = callback(PageBatch(page_numbers=[1, 2, 3]))
        assert out == []


class TestExtractCallbackPageSelection:
    def test_sends_all_relevant_pages_by_default(self) -> None:
        client = _StubClient(parsed=None)
        callback = build_extract_callback(
            client,
            model="gemini-flash-latest",
            rendered_pages_by_num=_rendered_pages([1, 2, 3, 4]),
        )

        callback([1, 2, 3, 4])

        parts = client.models.last_contents[0].parts
        # One prompt part, then one image part + one page-label part per page.
        assert len(parts) == 1 + (4 * 2)

    def test_respects_explicit_debug_page_cap(self) -> None:
        client = _StubClient(parsed=None)
        callback = build_extract_callback(
            client,
            model="gemini-flash-latest",
            rendered_pages_by_num=_rendered_pages([1, 2, 3, 4]),
            max_pages=2,
        )

        callback([1, 2, 3, 4])

        parts = client.models.last_contents[0].parts
        assert len(parts) == 1 + (2 * 2)
