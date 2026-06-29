import asyncio

from app.ai_implementation.pipeline.document_extraction_provider import DocumentPromptContext, RenderedPage
from app.models.financial_document_extraction import DocumentExtractionFailure
from app.models.reserve_study_extraction import (
    ExtractedReserveStudyDocument,
    ExtractedReserveStudyPage,
    ExtractedReserveStudyRow,
    ReserveStudyPageClassification,
    ReserveStudyPageRole,
)
from app.services.reserve_study_extractor import (
    canonicalize_reserve_study_row_dicts,
    canonicalize_reserve_study_rows,
    discover_reserve_study_pages,
    extract_reserve_study,
)


def _page_text(*pages: str) -> str:
    chunks: list[str] = []
    for index, text in enumerate(pages, start=1):
        chunks.append(f"--- Page {index} ---")
        chunks.append(text)
    return "\n".join(chunks)


def _rendered_pages(count: int) -> list[RenderedPage]:
    return [
        RenderedPage(page_number=index, mime_type="image/png", content=b"fake-png")
        for index in range(1, count + 1)
    ]


def _pages_from_batch_prompt(prompt: str) -> list[int]:
    return [
        int(line.removeprefix("Page: ").strip())
        for line in prompt.splitlines()
        if line.startswith("Page: ")
    ]


def test_negative_reserve_numeric_values_are_flagged_not_rejected():
    row = ExtractedReserveStudyRow.model_validate(
        {
            "row_id": "paint-negative",
            "line_item": "Paint",
            "useful_life": 5,
            "remaining_life": 1,
            "replacement_cost": 10000,
            "year_replacement_provision": -154,
        }
    )

    assert row.year_replacement_provision is None
    assert "invalid_negative_year_replacement_provision" in row.flags


def test_discovery_default_scans_beyond_previous_page_cap(monkeypatch, tmp_path):
    pages = [f"boilerplate page {index}" for index in range(1, 20)]
    pages.append(
        "Component Inventory\nUseful Life Remaining Life Quantity Replacement Cost\nRoof 20 2 1 250000"
    )

    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 20,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=20: _page_text(*pages[:max_pages]),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 20),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.RESERVE_TABLE if page_number == 20 else ReserveStudyPageRole.UNRELATED,
                    confidence=0.94 if page_number == 20 else 0.08,
                    reasons=["late-page reserve table"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "reserve-study.pdf")))

    assert result.page_spans[0].start_page == 20
    assert result.page_spans[0].end_page == 20


def test_reserve_study_vision_calls_use_shorter_timeouts(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 1,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=1: _page_text(
            "Component Inventory\nUseful Life Remaining Life Quantity Replacement Cost\nRoof 20 2 1 250000"
        ),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 1),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._render_reserve_study_page_subset",
        lambda path, page_numbers, dpi: {},
    )

    calls: list[dict[str, object]] = []

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0, context=None):
        calls.append({"schema": response_schema, "timeout": timeout, "context": context})
        if response_schema.__name__ == "_ReserveStudyBatchClassification":
            return response_schema(
                classifications=[
                    ReserveStudyPageClassification(
                        page_number=1,
                        role=ReserveStudyPageRole.RESERVE_TABLE,
                        confidence=0.94,
                        reasons=["reserve table"],
                    )
                ]
            )
        return ExtractedReserveStudyPage(
            rows=[
                ExtractedReserveStudyRow(
                    row_id="roof",
                    line_item="Roof",
                    useful_life=20,
                    remaining_life=2,
                    quantity="1",
                    replacement_cost=250000,
                    source_page=1,
                )
            ],
            confidence=0.93,
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(extract_reserve_study(str(tmp_path / "Old Mill Final 2024.pdf")))

    assert isinstance(result, ExtractedReserveStudyDocument)
    assert calls[0]["timeout"] == 60.0
    assert calls[1]["timeout"] == 60.0


def test_discovery_keeps_late_empty_text_pages_addressable_in_mixed_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 22,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=22: _page_text(
            "Annual disclosure cover with reserve study mention.",
            "General notes and insurance text only.",
        ),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 22),
    )

    seen_pages: list[int] = []
    call_count = 0

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        nonlocal call_count
        call_count += 1
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        seen_pages.extend(page_numbers)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.RESERVE_TABLE if page_number in {20, 21, 22} else ReserveStudyPageRole.UNRELATED,
                    confidence=0.95 if page_number in {20, 21, 22} else 0.1,
                    reasons=["late-page image-backed reserve table"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "mixed-reserve-study.pdf")))

    assert call_count == 3
    assert 20 in seen_pages
    assert [item.page_number for item in result.classifications if item.role == ReserveStudyPageRole.RESERVE_TABLE] == [20, 21, 22]
    assert result.page_spans[0].start_page == 20
    assert result.page_spans[0].end_page == 22


def test_discovery_is_image_first_and_does_not_attempt_ocr(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 22,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=22: (_ for _ in ()).throw(AssertionError("discovery should not use OCR/text extraction")),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 22),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.RESERVE_TABLE if page_number in {20, 21, 22} else ReserveStudyPageRole.UNRELATED,
                    confidence=0.96 if page_number in {20, 21, 22} else 0.1,
                    reasons=["image-first discovery"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "image-first-reserve-study.pdf")))

    assert result.page_spans[0].start_page == 20
    assert result.page_spans[0].end_page == 22


def test_discovery_uses_image_only_prompt_for_empty_text_pages_in_mixed_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 3,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=3: _page_text(
            "Reserve study cover page only."
        ),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 3),
    )

    prompts: dict[int, str] = {}

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        for page_number in page_numbers:
            prompts[page_number] = prompt
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.RESERVE_TABLE if page_number == 3 else ReserveStudyPageRole.UNRELATED,
                    confidence=0.92 if page_number == 3 else 0.15,
                    reasons=["image-only mixed-pdf detection"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "mixed-image-pages.pdf")))

    assert "NO TEXT LAYER" not in prompts[3]
    assert "rendered images only" in prompts[3]
    assert result.page_spans[0].start_page == 3
    assert result.page_spans[0].end_page == 3


def test_discovery_prefers_pages_with_reserve_lifecycle_headers(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=12: _page_text(
            "Welcome to the annual disclosure packet. Insurance, funding percent, and notes.",
            "Component Inventory\nUseful Life Remaining Life Quantity Replacement Cost\nRoof 20 2 1 250000",
        ),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(2),
    )

    seen_pages: list[int] = []

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        seen_pages.extend(page_numbers)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.RESERVE_TABLE if page_number == 2 else ReserveStudyPageRole.UNRELATED,
                    confidence=0.93 if page_number == 2 else 0.12,
                    reasons=["matched reserve lifecycle headers"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "reserve-study.pdf")))

    assert [item.page_number for item in result.classifications] == [1, 2]
    assert seen_pages == [1, 2]
    assert result.page_spans[0].start_page == 2
    assert result.page_spans[0].end_page == 2


def test_discovery_returns_page_spans_and_confidence(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=12: _page_text(
            "cover letter",
            "Reserve Summary\nProjected funding overview and reserve context.",
            "Component Inventory\nUseful Life Remaining Life Replacement Cost\nRoof 20 2 250000",
        ),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(3),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.RESERVE_CONTEXT if page_number == 2 else (
                        ReserveStudyPageRole.RESERVE_TABLE if page_number == 3 else ReserveStudyPageRole.UNRELATED
                    ),
                    confidence=0.72 if page_number == 2 else (0.95 if page_number == 3 else 0.05),
                    reasons=["adjacent reserve narrative" if page_number == 2 else "component table headers"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "reserve-study.pdf")))

    assert len(result.page_spans) == 1
    assert result.page_spans[0].start_page == 2
    assert result.page_spans[0].end_page == 3
    assert result.page_spans[0].confidence > 0.8


def test_discovery_promotes_context_page_between_table_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(3),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=(
                        ReserveStudyPageRole.RESERVE_TABLE
                        if page_number in {1, 3}
                        else ReserveStudyPageRole.RESERVE_CONTEXT
                    ),
                    confidence=0.95 if page_number in {1, 3} else 0.8,
                    reasons=["continuation page" if page_number == 2 else "table page"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "continuation-reserve-study.pdf")))

    table_pages = [item.page_number for item in result.classifications if item.role == ReserveStudyPageRole.RESERVE_TABLE]
    assert table_pages == [1, 2, 3]
    assert result.page_spans[0].start_page == 1
    assert result.page_spans[0].end_page == 3


def test_discovery_keeps_only_best_ui_table_sequence(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 24,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 24),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        classifications: list[dict[str, object]] = []
        for page_number in page_numbers:
            if page_number in {6, 7, 8}:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.82,
                        "reasons": ["summary-style component table"],
                        "ui_fields_present": ["line_item", "useful_life", "remaining_life", "replacement_cost"],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": page_number != 6,
                        "same_table_as_next": page_number != 8,
                        "table_title_hint": "Executive Summary Table",
                    }
                )
            elif page_number in {18, 19, 20}:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.95,
                        "reasons": ["full component schedule for editing"],
                        "ui_fields_present": ["line_item", "quantity", "useful_life", "remaining_life", "replacement_cost"],
                        "is_primary_ui_table": True,
                        "same_table_as_previous": page_number != 18,
                        "same_table_as_next": page_number != 20,
                        "table_title_hint": "Reserve Component List Detail",
                    }
                )
            else:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "unrelated",
                        "confidence": 0.1,
                        "reasons": ["not the editable reserve schedule"],
                        "ui_fields_present": [],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": False,
                        "same_table_as_next": False,
                        "table_title_hint": None,
                    }
                )

        return response_schema.model_validate({"classifications": classifications})

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "multi-table-reserve-study.pdf")))

    assert [(span.start_page, span.end_page) for span in result.page_spans] == [(18, 20)]
    kept_pages = [item.page_number for item in result.classifications if item.role != ReserveStudyPageRole.UNRELATED]
    assert kept_pages == [18, 19, 20]


def test_extraction_uses_only_best_ui_table_sequence(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 24,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 24),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_reserve_study_page_texts_for_pages",
        lambda path, page_numbers: {page_number: "" for page_number in page_numbers},
    )

    extracted_pages: list[int] = []

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        if response_schema is ExtractedReserveStudyPage:
            page_number = int(prompt.split("Page: ", 1)[1].splitlines()[0])
            extracted_pages.append(page_number)
            return ExtractedReserveStudyPage(
                rows=[
                    ExtractedReserveStudyRow(
                        row_id=f"row-{page_number}",
                        line_item=f"Component {page_number}",
                        useful_life=20,
                        remaining_life=2,
                        quantity=1,
                        replacement_cost=1000.0,
                        source_page=page_number,
                    )
                ],
                warnings=[],
                confidence=0.9,
            )

        page_numbers = _pages_from_batch_prompt(prompt)
        classifications: list[dict[str, object]] = []
        for page_number in page_numbers:
            if page_number in {6, 7, 8}:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.82,
                        "reasons": ["summary-style component table"],
                        "ui_fields_present": ["line_item", "useful_life", "remaining_life", "replacement_cost"],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": page_number != 6,
                        "same_table_as_next": page_number != 8,
                        "table_title_hint": "Executive Summary Table",
                    }
                )
            elif page_number in {18, 19, 20}:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.95,
                        "reasons": ["full component schedule for editing"],
                        "ui_fields_present": ["line_item", "quantity", "useful_life", "remaining_life", "replacement_cost"],
                        "is_primary_ui_table": True,
                        "same_table_as_previous": page_number != 18,
                        "same_table_as_next": page_number != 20,
                        "table_title_hint": "Reserve Component List Detail",
                    }
                )
            else:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "unrelated",
                        "confidence": 0.1,
                        "reasons": ["not the editable reserve schedule"],
                        "ui_fields_present": [],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": False,
                        "same_table_as_next": False,
                        "table_title_hint": None,
                    }
                )
        return response_schema.model_validate({"classifications": classifications})

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(extract_reserve_study(str(tmp_path / "multi-table-reserve-study.pdf")))

    assert isinstance(result, ExtractedReserveStudyDocument)
    assert extracted_pages == [18, 19, 20]
    assert [row.source_page for row in result.rows] == [18, 19, 20]


def test_discovery_prefers_tabular_schedule_over_component_detail_appendix(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 67,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 67),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        classifications: list[dict[str, object]] = []
        for page_number in page_numbers:
            if page_number in {18, 19, 20}:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.99,
                        "reasons": ["compact aligned component schedule"],
                        "ui_fields_present": ["line_item", "quantity", "useful_life", "remaining_life", "replacement_cost"],
                        "is_primary_ui_table": True,
                        "same_table_as_previous": page_number != 18,
                        "same_table_as_next": page_number != 20,
                        "table_title_hint": "Reserve Component List Detail",
                        "is_tabular_schedule": True,
                        "is_component_detail_appendix": False,
                    }
                )
            elif page_number == 45:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_context",
                        "confidence": 0.95,
                        "reasons": ["intro page for component details appendix"],
                        "ui_fields_present": [],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": False,
                        "same_table_as_next": True,
                        "table_title_hint": "Component Details",
                        "is_tabular_schedule": False,
                        "is_component_detail_appendix": True,
                    }
                )
            elif 46 <= page_number <= 67:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.95,
                        "reasons": ["one component per narrative detail block"],
                        "ui_fields_present": ["line_item", "quantity", "useful_life", "remaining_life", "replacement_cost"],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": True,
                        "same_table_as_next": page_number != 67,
                        "table_title_hint": "Component Details",
                        "is_tabular_schedule": False,
                        "is_component_detail_appendix": True,
                    }
                )
            else:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "unrelated",
                        "confidence": 0.1,
                        "reasons": ["not the editable reserve schedule"],
                        "ui_fields_present": [],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": False,
                        "same_table_as_next": False,
                        "table_title_hint": None,
                        "is_tabular_schedule": False,
                        "is_component_detail_appendix": False,
                    }
                )

        return response_schema.model_validate({"classifications": classifications})

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "tabular-vs-appendix.pdf")))

    assert [(span.start_page, span.end_page) for span in result.page_spans] == [(18, 20)]
    kept_pages = [item.page_number for item in result.classifications if item.role != ReserveStudyPageRole.UNRELATED]
    assert kept_pages == [18, 19, 20]


def test_discovery_trims_duplicate_component_repeat_pages_from_anchor_range(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 24,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 24),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        classifications: list[dict[str, object]] = []
        for page_number in page_numbers:
            if page_number == 20:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.98,
                        "reasons": ["base editable reserve schedule"],
                        "ui_fields_present": ["line_item", "quantity", "useful_life", "remaining_life", "replacement_cost"],
                        "is_primary_ui_table": True,
                        "same_table_as_previous": False,
                        "same_table_as_next": True,
                        "table_title_hint": "Forecasted Statements of Replacement Fund",
                        "is_tabular_schedule": True,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": True,
                        "is_duplicate_component_repeat_page": False,
                    }
                )
            elif page_number in {21, 22}:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.91,
                        "reasons": ["same components repeated with derived year-rollforward columns"],
                        "ui_fields_present": ["line_item", "replacement_cost"],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": True,
                        "same_table_as_next": page_number == 21,
                        "table_title_hint": "Forecasted Statements of Replacement Fund (Continued)",
                        "is_tabular_schedule": True,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": False,
                        "is_duplicate_component_repeat_page": True,
                    }
                )
            else:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "unrelated",
                        "confidence": 0.1,
                        "reasons": ["not the primary reserve-study schedule"],
                        "ui_fields_present": [],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": False,
                        "same_table_as_next": False,
                        "table_title_hint": None,
                        "is_tabular_schedule": False,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": False,
                        "is_duplicate_component_repeat_page": False,
                    }
                )

        return response_schema.model_validate({"classifications": classifications})

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "first-street-duplicate-pages.pdf")))

    assert [(span.start_page, span.end_page) for span in result.page_spans] == [(20, 20)]
    kept_pages = [item.page_number for item in result.classifications if item.role != ReserveStudyPageRole.UNRELATED]
    assert kept_pages == [20]


def test_discovery_treats_quantity_as_optional_when_scoring_primary_schedule(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 24,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 24),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        classifications: list[dict[str, object]] = []
        for page_number in page_numbers:
            if page_number in {6, 7}:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.95,
                        "reasons": ["core schedule fields including remaining life"],
                        "ui_fields_present": ["line_item", "useful_life", "remaining_life", "replacement_cost"],
                        "is_primary_ui_table": True,
                        "same_table_as_previous": page_number != 6,
                        "same_table_as_next": page_number != 7,
                        "table_title_hint": "Projected Expenditure Schedule",
                        "is_tabular_schedule": True,
                        "is_component_detail_appendix": False,
                    }
                )
            elif page_number in {18, 19}:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.97,
                        "reasons": ["quantity present but remaining life absent"],
                        "ui_fields_present": ["line_item", "quantity", "useful_life", "replacement_cost"],
                        "is_primary_ui_table": True,
                        "same_table_as_previous": page_number != 18,
                        "same_table_as_next": page_number != 19,
                        "table_title_hint": "Component Data",
                        "is_tabular_schedule": True,
                        "is_component_detail_appendix": False,
                    }
                )
            else:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "unrelated",
                        "confidence": 0.1,
                        "reasons": ["not the primary reserve-study schedule"],
                        "ui_fields_present": [],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": False,
                        "same_table_as_next": False,
                        "table_title_hint": None,
                        "is_tabular_schedule": False,
                        "is_component_detail_appendix": False,
                    }
                )

        return response_schema.model_validate({"classifications": classifications})

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "quantity-optional-selection.pdf")))

    assert [(span.start_page, span.end_page) for span in result.page_spans] == [(6, 7)]


def test_discovery_prefers_reserve_plan_schedule_over_year_provision_liability_schedule(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 60,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 60),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        classifications: list[dict[str, object]] = []
        for page_number in page_numbers:
            if page_number == 20:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.93,
                        "reasons": ["multi-year reserve-study component schedule"],
                        "ui_fields_present": ["line_item", "quantity", "useful_life", "remaining_life", "replacement_cost"],
                        "is_primary_ui_table": True,
                        "same_table_as_previous": False,
                        "same_table_as_next": True,
                        "table_title_hint": "Forecasted Statements of Replacement Fund",
                        "is_tabular_schedule": True,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": True,
                        "is_duplicate_component_repeat_page": False,
                        "is_year_provision_or_liability_schedule": False,
                    }
                )
            elif page_number in {21, 22}:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.9,
                        "reasons": ["same components repeated for later-year rollforward columns"],
                        "ui_fields_present": ["line_item", "replacement_cost"],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": True,
                        "same_table_as_next": page_number == 21,
                        "table_title_hint": "Forecasted Statements of Replacement Fund (Continued)",
                        "is_tabular_schedule": True,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": False,
                        "is_duplicate_component_repeat_page": True,
                        "is_year_provision_or_liability_schedule": False,
                    }
                )
            elif page_number in {47, 48}:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.99,
                        "reasons": ["year-specific replacement provision and liability schedule"],
                        "ui_fields_present": ["line_item", "useful_life", "remaining_life", "replacement_cost"],
                        "is_primary_ui_table": True,
                        "same_table_as_previous": page_number != 47,
                        "same_table_as_next": page_number != 48,
                        "table_title_hint": "Forecasted Schedule of Estimated Major Component Replacement Provision",
                        "is_tabular_schedule": True,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": True,
                        "is_duplicate_component_repeat_page": False,
                        "is_year_provision_or_liability_schedule": True,
                    }
                )
            else:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "unrelated",
                        "confidence": 0.1,
                        "reasons": ["not the primary reserve-study schedule"],
                        "ui_fields_present": [],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": False,
                        "same_table_as_next": False,
                        "table_title_hint": None,
                        "is_tabular_schedule": False,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": False,
                        "is_duplicate_component_repeat_page": False,
                        "is_year_provision_or_liability_schedule": False,
                    }
                )

        return response_schema.model_validate({"classifications": classifications})

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "first-street-preference.pdf")))

    assert [(span.start_page, span.end_page) for span in result.page_spans] == [(20, 20)]


def test_discovery_trims_trailing_pages_when_anchor_alone_has_full_primary_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 24,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 24),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        classifications: list[dict[str, object]] = []
        for page_number in page_numbers:
            if page_number == 20:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.95,
                        "reasons": ["anchor page with the full editable field set"],
                        "ui_fields_present": ["line_item", "useful_life", "remaining_life", "replacement_cost"],
                        "is_primary_ui_table": True,
                        "same_table_as_previous": False,
                        "same_table_as_next": True,
                        "table_title_hint": "Forecasted Statements of Replacement Fund",
                        "is_tabular_schedule": True,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": True,
                        "is_duplicate_component_repeat_page": False,
                        "is_year_provision_or_liability_schedule": False,
                    }
                )
            elif page_number == 21:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.9,
                        "reasons": ["continuation page with only future-year columns"],
                        "ui_fields_present": ["line_item"],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": True,
                        "same_table_as_next": True,
                        "table_title_hint": "Forecasted Statements of Replacement Fund (Continued)",
                        "is_tabular_schedule": True,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": False,
                        "is_duplicate_component_repeat_page": True,
                        "is_year_provision_or_liability_schedule": False,
                    }
                )
            elif page_number == 22:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.94,
                        "reasons": ["later page repeats rows and only keeps part of the core field set"],
                        "ui_fields_present": ["line_item", "useful_life", "replacement_cost"],
                        "is_primary_ui_table": True,
                        "same_table_as_previous": True,
                        "same_table_as_next": False,
                        "table_title_hint": "Forecasted Statements of Replacement Fund",
                        "is_tabular_schedule": True,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": True,
                        "is_duplicate_component_repeat_page": False,
                        "is_year_provision_or_liability_schedule": False,
                    }
                )
            else:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "unrelated",
                        "confidence": 0.1,
                        "reasons": ["not the primary reserve-study schedule"],
                        "ui_fields_present": [],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": False,
                        "same_table_as_next": False,
                        "table_title_hint": None,
                        "is_tabular_schedule": False,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": False,
                        "is_duplicate_component_repeat_page": False,
                        "is_year_provision_or_liability_schedule": False,
                    }
                )

        return response_schema.model_validate({"classifications": classifications})

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "anchor-range-trim.pdf")))

    assert [(span.start_page, span.end_page) for span in result.page_spans] == [(20, 20)]


def test_discovery_does_not_reuse_reserve_statement_page_classifier(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=12: _page_text(
            "Reserve Income Statement\nOperating transfer and cash balance summary only."
        ),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(1),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.UNRELATED,
                    confidence=0.18,
                    reasons=["fund statement is not a reserve study table"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(discover_reserve_study_pages(str(tmp_path / "reserve-statement.pdf")))

    assert result.page_spans == []
    assert result.classifications[0].role == ReserveStudyPageRole.UNRELATED


def test_scanned_reserve_study_falls_back_to_image_only_classification(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=12: (_ for _ in ()).throw(ValueError("PDF has no text layer (scanned). Upload text-based PDF or Excel.")),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(2),
    )

    prompts: list[str] = []

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        prompts.append(prompt)
        if response_schema is ExtractedReserveStudyPage:
            return ExtractedReserveStudyPage(
                rows=[
                    ExtractedReserveStudyRow(
                        row_id="row-1",
                        line_item="Roof",
                        useful_life=20,
                        remaining_life=2,
                        replacement_cost=250000.0,
                        source_page=2,
                    )
                ],
                warnings=[],
                confidence=0.84,
            )
        page_numbers = _pages_from_batch_prompt(prompt)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.RESERVE_TABLE if page_number == 2 else ReserveStudyPageRole.UNRELATED,
                    confidence=0.9 if page_number == 2 else 0.25,
                    reasons=["image-only reserve table detection"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(extract_reserve_study(str(tmp_path / "scanned-reserve-study.pdf")))

    assert isinstance(result, ExtractedReserveStudyDocument)
    assert result.rows[0].line_item == "Roof"
    assert result.extraction_metadata["ocr_fallback_pages"] == [2]
    assert any("image only" in prompt.lower() for prompt in prompts)


def test_extraction_prompt_forbids_computing_or_inferring_missing_values(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 1,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 1),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_reserve_study_page_texts_for_pages",
        lambda path, page_numbers: {1: "Component Data\nYear New Expect. Life Total Cost\nRoof 2014 10 209671"},
    )

    seen_system_prompts: list[str] = []

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        if response_schema is ExtractedReserveStudyPage:
            seen_system_prompts.append(messages[0]["content"])
            return ExtractedReserveStudyPage(
                rows=[
                    ExtractedReserveStudyRow(
                        row_id="row-1",
                        line_item="Roof",
                        useful_life=10,
                        remaining_life=None,
                        replacement_cost=209671.0,
                        source_page=1,
                    )
                ],
                warnings=[],
                confidence=0.82,
            )

        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.RESERVE_TABLE,
                    confidence=0.96,
                    reasons=["primary reserve schedule"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(extract_reserve_study(str(tmp_path / "no-derived-values.pdf")))

    assert isinstance(result, ExtractedReserveStudyDocument)
    assert seen_system_prompts
    system_prompt = seen_system_prompts[0].lower()
    assert "only extract values that are explicitly visible" in system_prompt
    assert "do not calculate, infer, or derive missing values" in system_prompt
    assert "year new" in system_prompt
    assert "expected life" in system_prompt


def test_extraction_prompt_allows_visible_field_synonyms_without_arithmetic(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 1,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 1),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_reserve_study_page_texts_for_pages",
        lambda path, page_numbers: {
            1: "Description Future Cost Useful Life Remaining Life\nRoof 213,165 20 16"
        },
    )

    seen_system_prompts: list[str] = []

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        if response_schema is ExtractedReserveStudyPage:
            seen_system_prompts.append(messages[0]["content"])
            return ExtractedReserveStudyPage(
                rows=[
                    ExtractedReserveStudyRow(
                        row_id="row-1",
                        line_item="Roof",
                        useful_life=20,
                        remaining_life=16,
                        replacement_cost=213165.0,
                        source_page=1,
                    )
                ],
                warnings=[],
                confidence=0.84,
            )

        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.RESERVE_TABLE,
                    confidence=0.96,
                    reasons=["primary reserve schedule"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(extract_reserve_study(str(tmp_path / "cost-synonyms.pdf")))

    assert isinstance(result, ExtractedReserveStudyDocument)
    assert seen_system_prompts
    system_prompt = seen_system_prompts[0].lower()
    assert "description" in system_prompt
    assert "component name" in system_prompt
    assert "est. life" in system_prompt
    assert "expect. life" in system_prompt
    assert "remaining useful life" in system_prompt
    assert "item quan." in system_prompt
    assert "future cost" in system_prompt
    assert "current cost" in system_prompt
    assert "estimated cost" in system_prompt
    assert "total cost" in system_prompt
    assert "do not reinterpret dates or years as direct field values" in system_prompt
    assert "do not combine multiple columns" in system_prompt


def test_canonicalize_reserve_rows_derives_yearly_provision_and_estimated_liability():
    rows, reference_year = canonicalize_reserve_study_rows(
        [
            ExtractedReserveStudyRow(
                row_id="lighting-1",
                line_item="Lighting - Exterior",
                useful_life=20,
                remaining_life=10,
                quantity="1",
                replacement_cost=1529.0,
                source_page=20,
            )
        ]
    )

    assert reference_year is None
    assert rows[0].year_replacement_provision == 76
    assert rows[0].estimated_liability == 764
    assert "missing_year_replacement_provision" not in rows[0].flags
    assert "missing_estimated_liability" not in rows[0].flags


def test_canonicalize_reserve_rows_preserves_header_rows_without_flags():
    rows, reference_year = canonicalize_reserve_study_rows(
        [
            ExtractedReserveStudyRow(
                row_id="header-1",
                row_type="header",
                line_item="Exterior Components",
                useful_life=10,
                remaining_life=2,
                replacement_cost=5000,
                flags=["missing_estimated_liability"],
                source_page=20,
            ),
            ExtractedReserveStudyRow(
                row_id="roof-1",
                line_item="Roof",
                useful_life=20,
                remaining_life=1,
                replacement_cost=100000,
                source_page=20,
            ),
        ],
        explicit_reference_year=2026,
    )

    assert reference_year == 2026
    assert rows[0].row_type == "header"
    assert rows[0].line_item == "Exterior Components"
    assert rows[0].useful_life is None
    assert rows[0].replacement_cost is None
    assert rows[0].flags == []
    assert rows[1].row_type == "item"
    assert rows[1].year_replacement_provision == 5000


def test_canonicalize_reserve_rows_makes_duplicate_row_ids_unique():
    rows, reference_year = canonicalize_reserve_study_rows(
        [
            ExtractedReserveStudyRow(
                row_id="section",
                row_type="header",
                line_item="Exterior Components",
            ),
            ExtractedReserveStudyRow(
                row_id="component",
                line_item="Roof",
                useful_life=20,
                remaining_life=1,
                replacement_cost=100000,
            ),
            ExtractedReserveStudyRow(
                row_id="section",
                row_type="header",
                line_item="Interior Components",
            ),
            ExtractedReserveStudyRow(
                row_id="component",
                line_item="Paint",
                useful_life=10,
                remaining_life=2,
                replacement_cost=50000,
            ),
            ExtractedReserveStudyRow(
                row_id="component-2",
                line_item="Flooring",
                useful_life=8,
                remaining_life=3,
                replacement_cost=24000,
            ),
            ExtractedReserveStudyRow(
                row_id="component",
                line_item="Lighting",
                useful_life=12,
                remaining_life=4,
                replacement_cost=36000,
            ),
        ],
        explicit_reference_year=2026,
    )

    assert reference_year == 2026
    assert [row.row_id for row in rows] == [
        "section",
        "component",
        "section-2",
        "component-2",
        "component-2-2",
        "component-3",
    ]
    assert [row.row_type for row in rows] == [
        "header",
        "item",
        "header",
        "item",
        "item",
        "item",
    ]
    assert rows[0].line_item == "Exterior Components"
    assert rows[2].line_item == "Interior Components"


def test_canonicalize_reserve_row_dicts_preserves_invalid_row_order_and_ids():
    rows, reference_year = canonicalize_reserve_study_row_dicts(
        [
            {
                "row_id": "section",
                "row_type": "header",
                "line_item": "Exterior Components",
            },
            {
                "row_id": "manual-blank",
                "row_type": "item",
                "line_item": "",
                "useful_life": None,
                "remaining_life": None,
                "replacement_cost": None,
            },
            {
                "row_id": "roof",
                "row_type": "item",
                "line_item": "Roof",
                "useful_life": 20,
                "remaining_life": 1,
                "replacement_cost": 100000,
            },
        ],
        explicit_reference_year=2026,
    )

    assert reference_year == 2026
    assert [row["row_id"] for row in rows] == ["section", "manual-blank", "roof"]
    assert rows[1]["line_item"] == ""
    assert rows[2]["year_replacement_provision"] == 5000


def test_canonicalize_reserve_row_dicts_makes_mixed_duplicate_row_ids_unique():
    rows, _ = canonicalize_reserve_study_row_dicts(
        [
            {
                "row_id": "component",
                "line_item": "Roof",
                "useful_life": 20,
                "remaining_life": 1,
                "replacement_cost": 100000,
            },
            {
                "row_id": "component",
                "line_item": "",
            },
            {
                "row_id": "component",
                "line_item": "Paint",
                "useful_life": 10,
                "remaining_life": 2,
                "replacement_cost": 50000,
            },
        ],
        explicit_reference_year=2026,
    )

    assert [row["row_id"] for row in rows] == ["component", "component-2", "component-3"]
    assert rows[1]["line_item"] == ""
    assert rows[2]["line_item"] == "Paint"


def test_canonicalize_reserve_rows_preserves_explicit_pdf_values_over_recomputation():
    rows, _ = canonicalize_reserve_study_rows(
        [
            ExtractedReserveStudyRow(
                row_id="roof-1",
                line_item="Roof",
                useful_life=20,
                remaining_life=10,
                quantity="1",
                replacement_cost=1529.0,
                year_replacement_provision=77,
                estimated_liability=765,
                source_page=20,
            )
        ]
    )

    assert rows[0].year_replacement_provision == 77
    assert rows[0].estimated_liability == 765


def test_canonicalize_reserve_rows_derives_remaining_life_from_year_new_and_reference_year():
    rows, reference_year = canonicalize_reserve_study_rows(
        [
            ExtractedReserveStudyRow(
                row_id="paint-1",
                line_item="Paint",
                useful_life=10,
                remaining_life=None,
                quantity="1 L.S.",
                replacement_cost=209671.0,
                year_new=2014,
                source_page=28,
            )
        ],
        explicit_reference_year=2023,
    )

    assert reference_year == 2023
    assert rows[0].reference_year == 2023
    assert rows[0].remaining_life == 1
    assert rows[0].year_replacement_provision == 20967
    assert rows[0].estimated_liability == 188704
    assert "missing_remaining_life" not in rows[0].flags


def test_canonicalize_reserve_rows_clamps_overdue_remaining_life_to_zero():
    rows, reference_year = canonicalize_reserve_study_rows(
        [
            ExtractedReserveStudyRow(
                row_id="gate-1",
                line_item="Gate",
                useful_life=5,
                remaining_life=None,
                quantity="1",
                replacement_cost=10000.0,
                year_new=2010,
                source_page=12,
            )
        ],
        explicit_reference_year=2023,
    )

    assert reference_year == 2023
    assert rows[0].remaining_life == 0
    assert rows[0].estimated_liability == 10000


def test_canonicalize_reserve_rows_clamps_future_year_new_to_full_useful_life():
    # RAC-style component-data formats carry YEAR NEW + EXPECT. LIFE but no
    # remaining-life column, so remaining life is derived. A future-dated
    # YEAR NEW (component scheduled to be placed in service after the study's
    # reference year) must not produce remaining_life > useful_life.
    rows, reference_year = canonicalize_reserve_study_rows(
        [
            ExtractedReserveStudyRow(
                row_id="asphalt-1",
                line_item="Asphalt Sealing & Repairs",
                useful_life=6,
                remaining_life=None,
                quantity="315 S.F.",
                replacement_cost=630.0,
                year_new=2026,
                source_page=18,
            )
        ],
        explicit_reference_year=2024,
    )

    assert reference_year == 2024
    # age floored at 0 -> remaining life == full useful life, never more.
    assert rows[0].remaining_life == 6
    assert rows[0].remaining_life <= rows[0].useful_life
    # not-yet-aged component carries no accrued liability.
    assert rows[0].estimated_liability == 0
    assert "missing_remaining_life" not in rows[0].flags


def test_extraction_keeps_rows_with_blank_fields_and_flags_them(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=12: _page_text(
            "Component Inventory\nUseful Life Remaining Life Quantity Replacement Cost\nRoof - 2 1 250000"
        ),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(1),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        if response_schema is ExtractedReserveStudyPage:
            return ExtractedReserveStudyPage(
                rows=[
                    ExtractedReserveStudyRow(
                        row_id="row-1",
                        line_item="Roof",
                        useful_life=None,
                        remaining_life=2,
                        quantity=None,
                        replacement_cost=250000.0,
                        source_page=1,
                    )
                ],
                warnings=[],
                confidence=0.76,
            )
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.RESERVE_TABLE,
                    confidence=0.91,
                    reasons=["component table headers"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(extract_reserve_study(str(tmp_path / "reserve-study.pdf")))

    assert isinstance(result, ExtractedReserveStudyDocument)
    assert result.rows[0].line_item == "Roof"
    assert "missing_useful_life" in result.rows[0].flags
    assert "missing_quantity" not in result.rows[0].flags


def test_extraction_preserves_textual_quantity_values(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 1,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=1: (_ for _ in ()).throw(AssertionError("global OCR should not run before discovery")),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 1),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_reserve_study_page_texts_for_pages",
        lambda path, page_numbers: {1: ""},
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        if response_schema is ExtractedReserveStudyPage:
            return ExtractedReserveStudyPage.model_validate(
                {
                    "rows": [
                        {
                            "row_id": "fence-1",
                            "line_item": "Fence",
                            "useful_life": 20,
                            "remaining_life": 4,
                            "quantity": "12 LF",
                            "replacement_cost": 18000.0,
                            "source_page": 1,
                        }
                    ],
                    "warnings": [],
                    "confidence": 0.88,
                }
            )

        page_numbers = _pages_from_batch_prompt(prompt)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.RESERVE_TABLE,
                    confidence=0.94,
                    reasons=["tabular reserve schedule"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(extract_reserve_study(str(tmp_path / "text-quantity.pdf")))

    assert isinstance(result, ExtractedReserveStudyDocument)
    assert result.rows[0].quantity == "12 LF"
    assert "missing_quantity" not in result.rows[0].flags


def test_extraction_dedupes_repeated_components_across_selected_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 3,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=3: (_ for _ in ()).throw(AssertionError("global OCR should not run before discovery")),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 3),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_reserve_study_page_texts_for_pages",
        lambda path, page_numbers: {page_number: "" for page_number in page_numbers},
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        if response_schema is ExtractedReserveStudyPage:
            page_number = int(prompt.split("Page: ", 1)[1].splitlines()[0])
            if page_number == 1:
                return ExtractedReserveStudyPage(
                    rows=[
                        ExtractedReserveStudyRow(
                            row_id="roof-p1",
                            line_item="Roof",
                            useful_life=20,
                            remaining_life=2,
                            quantity=1,
                            replacement_cost=250000.0,
                            source_page=1,
                        ),
                        ExtractedReserveStudyRow(
                            row_id="paint-p1",
                            line_item="Paint",
                            useful_life=10,
                            remaining_life=1,
                            quantity=1,
                            replacement_cost=50000.0,
                            source_page=1,
                        ),
                    ],
                    warnings=[],
                    confidence=0.92,
                )
            if page_number == 2:
                return ExtractedReserveStudyPage(
                    rows=[
                        ExtractedReserveStudyRow(
                            row_id="roof-p2",
                            line_item="Roof",
                            useful_life=None,
                            remaining_life=None,
                            quantity=None,
                            replacement_cost=250000.0,
                            source_page=2,
                        ),
                    ],
                    warnings=[],
                    confidence=0.81,
                )
            return ExtractedReserveStudyPage(
                rows=[
                    ExtractedReserveStudyRow(
                        row_id="paint-p3",
                        line_item="Paint",
                        useful_life=None,
                        remaining_life=1,
                        quantity=None,
                        replacement_cost=50000.0,
                        source_page=3,
                    ),
                ],
                warnings=[],
                confidence=0.79,
            )

        page_numbers = _pages_from_batch_prompt(prompt)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.RESERVE_TABLE,
                    confidence=0.95,
                    reasons=["continuation pages of the same reserve schedule"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(extract_reserve_study(str(tmp_path / "duplicate-components.pdf")))

    assert isinstance(result, ExtractedReserveStudyDocument)
    assert [row.line_item for row in result.rows] == ["Roof", "Paint"]
    assert result.rows[0].useful_life == 20
    assert result.rows[0].remaining_life == 2
    assert result.rows[0].quantity == "1"
    assert result.rows[0].replacement_cost == 250000.0
    assert result.rows[0].source_page == 1
    assert result.rows[1].source_page == 1


def test_extraction_attempts_ocr_only_for_selected_pages_and_falls_back_per_page(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 4,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=4: (_ for _ in ()).throw(AssertionError("global OCR should not run before discovery")),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 4),
    )

    ocr_requests: list[list[int]] = []
    extraction_prompts: dict[int, str] = {}

    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_reserve_study_page_texts_for_pages",
        lambda path, page_numbers: (
            ocr_requests.append(list(page_numbers)) or {
                2: "Component Inventory\nUseful Life Remaining Life Quantity Replacement Cost\nRoof 20 2 1 250000",
                3: "",
            }
        ),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        if response_schema is ExtractedReserveStudyPage:
            page_number = int(prompt.split("Page: ", 1)[1].splitlines()[0])
            extraction_prompts[page_number] = prompt
            return ExtractedReserveStudyPage(
                rows=[
                    ExtractedReserveStudyRow(
                        row_id=f"row-{page_number}",
                        line_item=f"Item {page_number}",
                        useful_life=20,
                        remaining_life=2,
                        replacement_cost=1000.0 * page_number,
                        source_page=page_number,
                    )
                ],
                warnings=[],
                confidence=0.8,
            )

        page_numbers = _pages_from_batch_prompt(prompt)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.RESERVE_CONTEXT if page_number == 2 else (
                        ReserveStudyPageRole.RESERVE_TABLE if page_number == 3 else ReserveStudyPageRole.UNRELATED
                    ),
                    confidence=0.94 if page_number in {2, 3} else 0.1,
                    reasons=["selected reserve table pages"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(extract_reserve_study(str(tmp_path / "ocr-scoped-reserve-study.pdf")))

    assert isinstance(result, ExtractedReserveStudyDocument)
    assert ocr_requests == [[3]]
    assert 2 not in extraction_prompts
    assert "no extracted ocr/text" in extraction_prompts[3].lower()


def test_extraction_uses_trimmed_anchor_range_for_duplicate_component_repeat_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._get_pdf_page_count",
        lambda path: 24,
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(max_pages or 24),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_reserve_study_page_texts_for_pages",
        lambda path, page_numbers: {page_number: "" for page_number in page_numbers},
    )

    extracted_pages: list[int] = []

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        if response_schema is ExtractedReserveStudyPage:
            page_number = int(prompt.split("Page: ", 1)[1].splitlines()[0])
            extracted_pages.append(page_number)
            return ExtractedReserveStudyPage(
                rows=[
                    ExtractedReserveStudyRow(
                        row_id=f"row-{page_number}",
                        line_item=f"Component {page_number}",
                        useful_life=20,
                        remaining_life=2,
                        quantity="1",
                        replacement_cost=1000.0,
                        source_page=page_number,
                    )
                ],
                warnings=[],
                confidence=0.9,
            )

        page_numbers = _pages_from_batch_prompt(prompt)
        classifications: list[dict[str, object]] = []
        for page_number in page_numbers:
            if page_number == 20:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.98,
                        "reasons": ["base editable reserve schedule"],
                        "ui_fields_present": ["line_item", "quantity", "useful_life", "remaining_life", "replacement_cost"],
                        "is_primary_ui_table": True,
                        "same_table_as_previous": False,
                        "same_table_as_next": True,
                        "table_title_hint": "Forecasted Statements of Replacement Fund",
                        "is_tabular_schedule": True,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": True,
                        "is_duplicate_component_repeat_page": False,
                    }
                )
            elif page_number in {21, 22}:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "reserve_table",
                        "confidence": 0.91,
                        "reasons": ["same components repeated with derived year-rollforward columns"],
                        "ui_fields_present": ["line_item", "replacement_cost"],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": True,
                        "same_table_as_next": page_number == 21,
                        "table_title_hint": "Forecasted Statements of Replacement Fund (Continued)",
                        "is_tabular_schedule": True,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": False,
                        "is_duplicate_component_repeat_page": True,
                    }
                )
            else:
                classifications.append(
                    {
                        "page_number": page_number,
                        "role": "unrelated",
                        "confidence": 0.1,
                        "reasons": ["not the primary reserve-study schedule"],
                        "ui_fields_present": [],
                        "is_primary_ui_table": False,
                        "same_table_as_previous": False,
                        "same_table_as_next": False,
                        "table_title_hint": None,
                        "is_tabular_schedule": False,
                        "is_component_detail_appendix": False,
                        "adds_new_component_rows": False,
                        "is_duplicate_component_repeat_page": False,
                    }
                )

        return response_schema.model_validate({"classifications": classifications})

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(extract_reserve_study(str(tmp_path / "first-street-duplicate-pages.pdf")))

    assert isinstance(result, ExtractedReserveStudyDocument)
    assert extracted_pages == [20]
    assert [row.source_page for row in result.rows] == [20]


def test_extraction_returns_review_required_when_no_reserve_pages_found(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.reserve_study_extractor._extract_pdf_text_table",
        lambda path, max_pages=12: _page_text(
            "Annual budget notes only.",
            "Operating expenses and insurance commentary.",
        ),
    )
    monkeypatch.setattr(
        "app.services.reserve_study_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: _rendered_pages(2),
    )

    async def _fake_call(messages, response_schema, temperature=0.0, timeout=120.0):
        prompt = messages[-1]["content"][0]["text"]
        page_numbers = _pages_from_batch_prompt(prompt)
        return response_schema(
            classifications=[
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.UNRELATED,
                    confidence=0.1,
                    reasons=["no reserve-study table detected"],
                )
                for page_number in page_numbers
            ]
        )

    monkeypatch.setattr("app.services.reserve_study_extractor.call_llm_vision", _fake_call)

    result = asyncio.run(extract_reserve_study(str(tmp_path / "not-a-reserve-study.pdf")))

    assert isinstance(result, DocumentExtractionFailure)
    assert result.code == "reserve_pages_not_found"


# ─────────────────────────────────────────────────────────────────────────────
# Wrong-section parser fixes (drifting-puzzling-grove plan)
# ─────────────────────────────────────────────────────────────────────────────


def test_drop_annual_schedule_noise_filters_known_headers():
    """Rows whose line_item matches an annual-schedule header pattern
    (e.g., "Replacement Year 2031", "Total for 2031", "No Replacement in 2032",
    "Description Expenditures") are dropped from the extracted set even when
    they have numeric values."""
    from app.services.reserve_study_extractor import _drop_annual_schedule_noise

    rows = [
        ExtractedReserveStudyRow(
            row_id="r1", row_type="item", line_item="Replacement Year 2031",
            useful_life=None, remaining_life=None, replacement_cost=None,
        ),
        ExtractedReserveStudyRow(
            row_id="r2", row_type="item", line_item="Total for 2031",
            useful_life=None, remaining_life=None, replacement_cost=329932.0,
        ),
        ExtractedReserveStudyRow(
            row_id="r3", row_type="item", line_item="No Replacement in 2032",
            useful_life=None, remaining_life=None, replacement_cost=None,
        ),
        ExtractedReserveStudyRow(
            row_id="r4", row_type="item", line_item="Description Expenditures",
            useful_life=None, remaining_life=None, replacement_cost=None,
        ),
        ExtractedReserveStudyRow(
            row_id="r5", row_type="item",
            line_item="Painting - Building; Exterior",
            useful_life=10, remaining_life=6, replacement_cost=105853.0,
        ),
    ]
    kept, dropped = _drop_annual_schedule_noise(rows)
    assert dropped == 4
    assert [r.line_item for r in kept] == ["Painting - Building; Exterior"]


def test_drop_annual_schedule_noise_drops_rows_without_lifecycle_data():
    """Item rows with no useful_life AND no remaining_life AND no
    replacement_cost cannot be real components — drop them. A row with any
    one of those three set survives so partial extractions still flow
    through the missing_* flag system."""
    from app.services.reserve_study_extractor import _drop_annual_schedule_noise

    rows = [
        ExtractedReserveStudyRow(
            row_id="empty1", row_type="item", line_item="Mystery Item",
            useful_life=None, remaining_life=None, replacement_cost=None,
        ),
        ExtractedReserveStudyRow(
            row_id="partial1", row_type="item", line_item="Has useful_life only",
            useful_life=20, remaining_life=None, replacement_cost=None,
        ),
        ExtractedReserveStudyRow(
            row_id="partial2", row_type="item", line_item="Has cost only",
            useful_life=None, remaining_life=None, replacement_cost=1000.0,
        ),
    ]
    kept, dropped = _drop_annual_schedule_noise(rows)
    assert dropped == 1
    assert {r.line_item for r in kept} == {"Has useful_life only", "Has cost only"}


def test_drop_annual_schedule_noise_preserves_headers():
    """Header rows are rendered as visual breaks in the UI, not components.
    They must survive the noise filter regardless of their line_item text."""
    from app.services.reserve_study_extractor import _drop_annual_schedule_noise

    rows = [
        ExtractedReserveStudyRow(
            row_id="h1", row_type="header",
            line_item="Replacement Year 2031",  # would match noise regex but it's a header
        ),
        ExtractedReserveStudyRow(
            row_id="h2", row_type="header", line_item="Component Funding Summary",
        ),
    ]
    kept, dropped = _drop_annual_schedule_noise(rows)
    assert dropped == 0
    assert len(kept) == 2


def test_sequence_score_disqualifies_annual_schedule_span():
    """A short clean component-summary span beats a longer span that
    contains any page flagged is_year_provision_or_liability_schedule=True,
    regardless of how many pages or columns the longer span has.

    This is the gate that fixes the user-reported bug — the 8-page Annual
    Expenditure Detail used to beat the 3-page Component Funding Summary.
    """
    from app.services.reserve_study_extractor import (
        _ReserveStudySequence,
        _sequence_score,
    )

    def _cls(page, *, year_provision=False, primary=True, fields=None):
        return ReserveStudyPageClassification(
            page_number=page,
            role=ReserveStudyPageRole.RESERVE_TABLE,
            confidence=0.9,
            ui_fields_present=fields or ["line_item", "useful_life", "remaining_life", "replacement_cost"],
            is_primary_ui_table=primary,
            is_tabular_schedule=True,
            is_year_provision_or_liability_schedule=year_provision,
            adds_new_component_rows=True,
        )

    # Clean 3-page Component Funding Summary.
    clean = _ReserveStudySequence(items=[_cls(7), _cls(8), _cls(9)])
    # Longer 8-page Annual Expenditure Detail (every page flagged year_provision).
    dirty = _ReserveStudySequence(items=[
        _cls(p, year_provision=True, fields=["line_item", "replacement_cost"])
        for p in range(10, 18)
    ])

    assert _sequence_score(clean) > _sequence_score(dirty), (
        "Clean component-summary span must outrank any span containing "
        "an annual-schedule / year-provision page"
    )


def test_discovery_prompt_warns_against_annual_expenditure_schedules():
    """Regression guard: the prompt must describe the page STRUCTURE (year-
    organized, one or two numeric columns, missing lifecycle data) rather
    than rely on vendor-specific titles. Different vendors use different
    labels (Annual Expenditure Detail, Cash Flow Detail, Cash Flow Summary,
    Funding Analysis), and the classifier must recognize the pattern across
    all of them — including vendors not represented in our example PDFs."""
    from app.services.reserve_study_extractor import _DISCOVERY_SYSTEM_PROMPT

    # Structural language (vendor-agnostic — this is what generalizes to
    # PDFs we have not seen).
    assert "organized by year" in _DISCOVERY_SYSTEM_PROMPT
    assert "exactly once" in _DISCOVERY_SYSTEM_PROMPT.lower()
    assert "match on structure" in _DISCOVERY_SYSTEM_PROMPT
    # Known vendor labels appear as concrete fingerprints to anchor on,
    # but are explicitly framed in the prompt as non-exhaustive examples.
    for vendor_label in (
        "Annual Expenditure Detail",
        "Cash Flow Detail",
        "Cash Flow Funding Plan",
        "Funding Analysis",
    ):
        assert vendor_label in _DISCOVERY_SYSTEM_PROMPT, vendor_label
    for primary_label in (
        "Component Funding Summary",
        "Component Inventory",
        "Component Details",
    ):
        assert primary_label in _DISCOVERY_SYSTEM_PROMPT, primary_label


def test_drop_annual_schedule_noise_filters_cross_vendor_year_patterns():
    """The noise regex must catch year-organized headers across vendors,
    not just CIRMS-style 'Replacement Year YYYY'."""
    from app.services.reserve_study_extractor import _drop_annual_schedule_noise

    noisy_labels = [
        "Replacement Year 2031",
        "Expenditures for 2028",
        "2030 Expenditures",
        "Year-by-Year",
        "Cash Flow Detail",
        "Cash Flow Funding Plan",
        "Funding Analysis",
        "Annual Cash Flow",
        "Subtotal",
        "Grand Total",
    ]
    rows = [
        ExtractedReserveStudyRow(
            row_id=f"r{i}", row_type="item", line_item=label,
            useful_life=10, remaining_life=5, replacement_cost=1000.0,
        )
        for i, label in enumerate(noisy_labels)
    ]
    rows.append(
        ExtractedReserveStudyRow(
            row_id="real", row_type="item",
            line_item="Roof - Built Up; Replacement",
            useful_life=20, remaining_life=16, replacement_cost=213165.0,
        )
    )
    kept, dropped = _drop_annual_schedule_noise(rows)
    assert dropped == len(noisy_labels)
    assert [r.line_item for r in kept] == ["Roof - Built Up; Replacement"]


def test_drop_annual_schedule_noise_handles_unknown_vendor_via_lifecycle_check():
    """Vendor-agnostic backstop: even if the noise regex doesn't recognize
    a year header from an unseen vendor, the lifecycle-data check still
    drops rows that lack useful_life AND remaining_life AND replacement_cost.

    This is the primary defense for PDFs from vendors we have not seen —
    a row with no lifecycle data cannot be a real reserve component
    regardless of its label."""
    from app.services.reserve_study_extractor import _drop_annual_schedule_noise

    rows = [
        # Unknown-vendor year-band label the regex won't catch:
        ExtractedReserveStudyRow(
            row_id="unseen1", row_type="item",
            line_item="Reserve Component Outlook: FY 2031",
            useful_life=None, remaining_life=None, replacement_cost=None,
        ),
        # An arbitrary section divider with no real data:
        ExtractedReserveStudyRow(
            row_id="unseen2", row_type="item", line_item="*** Replacement Phase III ***",
            useful_life=None, remaining_life=None, replacement_cost=None,
        ),
        # A real component still passes through.
        ExtractedReserveStudyRow(
            row_id="real", row_type="item",
            line_item="Some New Vendor Component Name",
            useful_life=15, remaining_life=8, replacement_cost=42000.0,
        ),
    ]
    kept, dropped = _drop_annual_schedule_noise(rows)
    assert dropped == 2
    assert [r.line_item for r in kept] == ["Some New Vendor Component Name"]
