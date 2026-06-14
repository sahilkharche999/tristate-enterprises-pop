from __future__ import annotations

from app.dre_extraction.prompts.assessment_mapping_review_assistant import (
    PROMPT_TEXT,
    PROMPT_VERSION,
)


def test_prompt_uses_only_current_hoa_approved_setup_context() -> None:
    lowered = PROMPT_TEXT.lower()
    assert "approved dre setup json" in lowered
    assert "this specific association" in lowered
    assert "do not re-read the pdf" in lowered
    assert "do not invent pools" in lowered


def test_prompt_requires_decision_groups_and_blocks_final_engine_math() -> None:
    lowered = PROMPT_TEXT.lower()
    assert "safe_to_stage" in PROMPT_TEXT
    assert "needs_decision" in PROMPT_TEXT
    assert "exclude_from_mapping" in PROMPT_TEXT
    assert "residual_equal_preview" in PROMPT_TEXT
    assert "not final engine math" in lowered
    assert "not final authority" in lowered


def test_prompt_teaches_special_dre_mapping_types_without_hardcoded_hoa_examples() -> None:
    lowered = PROMPT_TEXT.lower()
    assert "equal_base" in PROMPT_TEXT
    assert "prorated_variable" in PROMPT_TEXT
    assert "exemption_credit" in PROMPT_TEXT
    assert "reserve_component" in PROMPT_TEXT
    assert "unknown_needs_review" in PROMPT_TEXT
    assert "old mill" not in lowered
    assert "esprit park" not in lowered


def test_prompt_version_for_assessment_mapping_ai_assistant() -> None:
    assert PROMPT_VERSION == "1.0.0"
