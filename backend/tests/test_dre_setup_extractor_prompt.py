from __future__ import annotations

from app.dre_extraction.prompts.dre_setup_extractor import (
    PROMPT_TEXT,
    PROMPT_VERSION,
)


def test_prompt_teaches_generic_residual_pool_derivation() -> None:
    lowered = PROMPT_TEXT.lower()
    assert "budget_line_derivation" in PROMPT_TEXT
    assert "residual_default" in PROMPT_TEXT
    assert "total" in lowered
    assert "minus" in lowered
    assert "remaining" in lowered or "balance" in lowered
    assert "base" in lowered or "equal" in lowered


def test_prompt_requires_review_for_empty_lines_without_residual_evidence() -> None:
    lowered = PROMPT_TEXT.lower()
    assert "empty" in lowered
    assert "included_budget_lines" in PROMPT_TEXT
    assert "without residual" in lowered or "no residual" in lowered
    assert "review" in lowered


def test_prompt_does_not_hardcode_known_hoa_or_page_examples() -> None:
    forbidden_fragments = [
        "396 first",
        "100 first",
        "old mill",
        "esprit park",
        "page 6",
        "$102,451",
        "$24,642",
        "$4,800",
    ]
    lowered = PROMPT_TEXT.lower()
    for fragment in forbidden_fragments:
        assert fragment not in lowered


def test_prompt_extracts_budget_line_mapping_evidence_without_using_dre_dollars() -> None:
    lowered = PROMPT_TEXT.lower()
    assert "step 5e" in lowered
    assert "budget_line_mapping_evidence" in PROMPT_TEXT
    assert "do not use these old dre dollar amounts as current-year budget amounts" in lowered
    assert "source_evidence_text" in PROMPT_TEXT
    assert "assessment_type" in PROMPT_TEXT


def test_prompt_version_bumped_for_budget_mapping_evidence_change() -> None:
    assert PROMPT_VERSION == "2.4.0"
