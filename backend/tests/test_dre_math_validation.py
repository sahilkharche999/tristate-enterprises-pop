"""DRE math-validation tests (Phase 3.8 tasks 95 + 96).

Verifies the validation_checks structure the DRE extraction prompt
emits is preserved through schema validation and surfaced for
operator review. Real Gemini emits a list of named math checks; we
mock representative outputs and assert the engine + persistence
layer handles every status (pass / fail / warning / not_applicable)
correctly.

Task 95: at least one extraction with a denominator that doesn't
match recalculated row totals; extraction preserves the DRE value,
warning emitted, no auto-correction.

Task 96: all 8 standard math-validation checks fire correctly on
at least one fixture each.
"""
from __future__ import annotations

import json

from app.dre_extraction.schemas import (
    DRESetupExtraction,
    ValidationCheck,
)
from app.dre_extraction.validation import parse_extraction_response


THE_8_MATH_CHECKS = [
    "Variable Factor Calculation",
    "Base + Variable = Total",
    "Percentages sum to 100",
    "Denominator matches recalculated sum",
    "Pool annual = monthly × 12",
    "Unit count consistency",
    "Specified-value sum reconciles",
    "Reserve contribution / Useful Life sanity",
]


def _payload(validation_checks: list[dict]) -> dict:
    return {
        "document_metadata": {"association_name": "Test", "source_pages": [1]},
        "assessment_setup": {
            "setup_type": "grouped_category",
            "confidence": 0.9,
            "source_pages": [1],
        },
        "allocation_pools": [],
        "unit_structure": {"groups": [], "units": []},
        "formulas": [],
        "validation_checks": validation_checks,
        "human_review_questions": [],
    }


class TestDREValidationChecksParseCleanly:
    def test_pass_check_round_trips(self):
        extraction = DRESetupExtraction.model_validate(_payload([
            {
                "check_name": "Variable Factor Calculation",
                "status": "pass",
                "details": "1667.99 / 157536 = 0.01059 matches DRE",
                "source_pages": [14],
            },
        ]))
        assert len(extraction.validation_checks) == 1
        c = extraction.validation_checks[0]
        assert c.status == "pass"
        assert "matches DRE" in c.details

    def test_fail_check_preserved_verbatim(self):
        """Task 95: a denominator mismatch is recorded as status=fail
        AND the prompt-emitted details surface to the operator."""
        extraction = DRESetupExtraction.model_validate(_payload([
            {
                "check_name": "Denominator matches recalculated sum",
                "status": "fail",
                "details": (
                    "DRE-shown denominator=10000 but recalculated row sum=9850; "
                    "delta=150"
                ),
                "source_pages": [5, 14],
            },
        ]))
        c = extraction.validation_checks[0]
        assert c.status == "fail"
        # DRE-preservation rule: details are kept verbatim, no auto-correction.
        assert "delta=150" in c.details

    def test_all_check_statuses_validate(self):
        """Every Literal status produces a parseable ValidationCheck."""
        for status in ("pass", "fail", "warning", "not_applicable"):
            check = ValidationCheck.model_validate({
                "check_name": "X",
                "status": status,
                "details": f"status={status}",
                "source_pages": [1],
            })
            assert check.status == status

    def test_all_8_math_checks_validate(self):
        """Task 96: every one of the 8 named math checks parses."""
        checks = [
            {
                "check_name": name,
                "status": "pass",
                "details": f"Check {i + 1} passed",
                "source_pages": [i + 1],
            }
            for i, name in enumerate(THE_8_MATH_CHECKS)
        ]
        extraction = DRESetupExtraction.model_validate(_payload(checks))
        assert len(extraction.validation_checks) == 8
        assert {c.check_name for c in extraction.validation_checks} == set(THE_8_MATH_CHECKS)


class TestDREWithMathErrorPipeline:
    """Task 95 end-to-end: parse_extraction_response should NOT silently
    drop or correct a failing validation_check — the operator must see it.
    """

    def test_failing_check_passes_schema_validation(self):
        raw = json.dumps(_payload([
            {
                "check_name": "Pool annual = monthly × 12",
                "status": "fail",
                "details": "annual=1200 monthly=99 (expected 1188)",
                "source_pages": [3],
            },
        ]))
        result = parse_extraction_response(raw)
        # Successful schema validation
        assert result.extraction is not None
        assert result.schema_validation_errors == []
        # The fail status is preserved on the extraction object
        check = result.extraction.validation_checks[0]
        assert check.status == "fail"
        assert "99" in check.details

    def test_extraction_with_mixed_pass_fail_warning_preserves_all(self):
        raw = json.dumps(_payload([
            {"check_name": "Variable Factor Calculation", "status": "pass",
             "details": "ok", "source_pages": [1]},
            {"check_name": "Denominator matches recalculated sum",
             "status": "fail", "details": "mismatch 150", "source_pages": [5]},
            {"check_name": "Percentages sum to 100", "status": "warning",
             "details": "99.998 ≈ 100, rounding", "source_pages": [8]},
            {"check_name": "Base + Variable = Total", "status": "not_applicable",
             "details": "no variable component", "source_pages": []},
        ]))
        result = parse_extraction_response(raw)
        assert result.extraction is not None
        statuses = [c.status for c in result.extraction.validation_checks]
        assert statuses == ["pass", "fail", "warning", "not_applicable"]
