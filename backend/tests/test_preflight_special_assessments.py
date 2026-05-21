"""Preflight: special assessments (Phase 4.4).

Read-time status inference for legacy rows (no explicit ``status``)
plus blocking-error checks for the three failure modes:
- approved_scheduled missing amount
- approved_scheduled missing due_date / due_dates
- possible_disclosure_only missing display_language
"""
from __future__ import annotations

import pytest

from app.disclosure_package.preflight import (
    check_special_assessments,
    infer_special_assessment_status,
)


class TestInferStatusFromLegacyShape:
    def test_explicit_status_honored(self) -> None:
        entry = {"status": "approved_scheduled", "amount_per_unit": 0}
        # Explicit status wins even if amount=0 would normally infer 'none'
        assert infer_special_assessment_status(entry) == "approved_scheduled"

    def test_amount_per_unit_positive_infers_approved_scheduled(self) -> None:
        assert (
            infer_special_assessment_status({"amount_per_unit": 250.0})
            == "approved_scheduled"
        )

    def test_legacy_per_unit_field_used(self) -> None:
        # Older entries used "per_unit"; the inference still works
        assert (
            infer_special_assessment_status({"per_unit": 100.0})
            == "approved_scheduled"
        )

    def test_amount_zero_with_display_language_infers_possible_disclosure(self) -> None:
        entry = {
            "amount_per_unit": 0,
            "display_language": "The Board may impose a special assessment.",
        }
        assert (
            infer_special_assessment_status(entry) == "possible_disclosure_only"
        )

    def test_amount_zero_no_display_language_infers_none(self) -> None:
        assert (
            infer_special_assessment_status({"amount_per_unit": 0}) == "none"
        )

    def test_empty_dict_infers_none(self) -> None:
        assert infer_special_assessment_status({}) == "none"

    def test_unknown_explicit_status_falls_through_to_inference(self) -> None:
        entry = {"status": "made_up", "amount_per_unit": 100.0}
        # Unknown explicit value is ignored; inference runs over the other fields
        assert infer_special_assessment_status(entry) == "approved_scheduled"


class TestApprovedScheduledChecks:
    def test_missing_amount_blocks(self) -> None:
        results = check_special_assessments(entries=[
            {
                "label": "Roof repair",
                "status": "approved_scheduled",
                "due_date": "2026-06-01",
                # amount_per_unit omitted
            },
        ])
        codes = [(r.field_path, r.severity) for r in results]
        assert ("special_assessments[0].amount_per_unit", "blocking") in codes

    def test_zero_amount_blocks(self) -> None:
        results = check_special_assessments(entries=[
            {
                "label": "Stub",
                "status": "approved_scheduled",
                "amount_per_unit": 0,
                "due_date": "2026-06-01",
            },
        ])
        assert any(
            "amount_per_unit" in r.field_path and r.severity == "blocking"
            for r in results
        )

    def test_missing_both_due_date_fields_blocks(self) -> None:
        results = check_special_assessments(entries=[
            {
                "label": "Re-roof",
                "status": "approved_scheduled",
                "amount_per_unit": 500.0,
                # neither due_date nor due_dates
            },
        ])
        assert any(
            "due_date" in r.field_path and r.severity == "blocking"
            for r in results
        )

    def test_due_dates_array_satisfies_requirement(self) -> None:
        results = check_special_assessments(entries=[
            {
                "label": "Multi-installment",
                "status": "approved_scheduled",
                "amount_per_unit": 500.0,
                "due_dates": ["2026-06-01", "2026-12-01"],
            },
        ])
        assert results == []

    def test_due_date_string_satisfies_requirement(self) -> None:
        results = check_special_assessments(entries=[
            {
                "label": "Single installment",
                "status": "approved_scheduled",
                "amount_per_unit": 500.0,
                "due_date": "2026-06-01",
            },
        ])
        assert results == []


class TestPossibleDisclosureOnlyChecks:
    def test_missing_display_language_blocks(self) -> None:
        results = check_special_assessments(entries=[
            {
                "label": "Possible future SA",
                "status": "possible_disclosure_only",
                "amount_per_unit": 0,
                # display_language missing
            },
        ])
        assert any(
            "display_language" in r.field_path and r.severity == "blocking"
            for r in results
        )

    def test_whitespace_display_language_blocks(self) -> None:
        results = check_special_assessments(entries=[
            {
                "label": "Stub",
                "status": "possible_disclosure_only",
                "amount_per_unit": 0,
                "display_language": "   ",
            },
        ])
        assert results[0].severity == "blocking"

    def test_present_display_language_passes(self) -> None:
        results = check_special_assessments(entries=[
            {
                "label": "Possible SA",
                "status": "possible_disclosure_only",
                "amount_per_unit": 0,
                "display_language": "The Board may impose a special assessment.",
            },
        ])
        assert results == []


class TestLegacyEntriesInferred:
    def test_legacy_entry_with_amount_but_no_status_treated_as_approved(self) -> None:
        # No explicit status; amount>0 → inferred approved_scheduled
        # But no due_date → blocking
        results = check_special_assessments(entries=[
            {"label": "Legacy SA", "amount_per_unit": 250.0},
        ])
        assert any(
            "due_date" in r.field_path and r.severity == "blocking"
            for r in results
        )

    def test_legacy_entry_with_amount_and_due_date_passes(self) -> None:
        results = check_special_assessments(entries=[
            {
                "label": "Legacy SA",
                "amount_per_unit": 250.0,
                "due_date": "2026-09-01",
            },
        ])
        assert results == []

    def test_legacy_entry_with_zero_amount_inferred_none_no_checks(self) -> None:
        # status='none' → no preflight rules apply
        results = check_special_assessments(entries=[
            {"label": "Inactive", "amount_per_unit": 0},
        ])
        assert results == []


class TestMultipleEntries:
    def test_mix_of_valid_and_invalid_returns_only_invalid_findings(self) -> None:
        entries = [
            # 0: valid approved_scheduled
            {
                "status": "approved_scheduled",
                "amount_per_unit": 500.0,
                "due_date": "2026-06-01",
            },
            # 1: invalid approved_scheduled (no due date)
            {
                "status": "approved_scheduled",
                "amount_per_unit": 500.0,
            },
            # 2: valid possible_disclosure_only
            {
                "status": "possible_disclosure_only",
                "display_language": "Maybe.",
            },
            # 3: invalid possible_disclosure_only (no language)
            {
                "status": "possible_disclosure_only",
            },
        ]
        results = check_special_assessments(entries=entries)
        paths = [r.field_path for r in results]
        assert "special_assessments[1].due_date" in paths
        assert "special_assessments[3].display_language" in paths
        assert "special_assessments[0]" not in " ".join(paths)
        assert "special_assessments[2]" not in " ".join(paths)
