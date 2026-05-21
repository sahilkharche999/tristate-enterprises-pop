"""Phase 1.4 of dre-driven-assessment-engine: income-statement column
picker correctness.

The DRE-driven assessment engine consumes ``BudgetDraft.line_items.amount``
as annual by invariant. The parser MUST raise
``IncomeStatementMissingAnnualColumn`` when it can't find the Annual
Budget column, so a hardcoded-fallback column index never silently
promotes YTD or current-period values as if they were annual dues.

Tests:
- ``parse_rows_with_sections_strict`` raises when annual_budget came
  from fallback only (no real header match)
- Strict mode passes through when annual_budget was a real match
- Promoted line items carry ``source_column`` audit field
"""
from __future__ import annotations

import pytest

from app.services.income_statement_parser import (
    IncomeStatementMissingAnnualColumn,
    parse_rows_with_sections,
    parse_rows_with_sections_strict,
)


class TestStrictRejectsFallbackOnly:
    def test_raises_when_no_real_match(self) -> None:
        # col_indices simulating Tier-3 fallback: no _real_matched_keys
        col_indices = {
            "ytd_actual": 19,
            "annual_budget": 32,
            "variance": 26,
            "_detection_tier": 3,
            "_real_matched_keys": [],
        }
        with pytest.raises(IncomeStatementMissingAnnualColumn) as ctx:
            parse_rows_with_sections_strict([], col_indices)
        assert ctx.value.detected_columns["_detection_tier"] == 3

    def test_raises_when_other_keys_real_but_not_annual_budget(self) -> None:
        # Detector found ytd_actual and variance but not annual_budget
        col_indices = {
            "ytd_actual": 5,
            "variance": 7,
            "annual_budget": 32,  # fallback
            "_detection_tier": 1,
            "_real_matched_keys": ["ytd_actual", "variance"],
        }
        with pytest.raises(IncomeStatementMissingAnnualColumn):
            parse_rows_with_sections_strict([], col_indices)


class TestStrictAcceptsRealMatch:
    def test_passes_when_annual_budget_in_real_matched(self) -> None:
        # Simulated rows: one header row, one data row in "Income" section
        rows = [
            [None, None, None, None, None],
            ["Income", None, None, None, None],
            [None, "5000 - HOA Dues", None, None, 12000.0],
        ]
        col_indices = {
            "ytd_actual": 2,
            "annual_budget": 4,
            "variance": 3,
            "_detection_tier": 1,
            "_real_matched_keys": ["annual_budget", "ytd_actual"],
        }
        items = parse_rows_with_sections_strict(rows, col_indices)
        assert len(items) == 1
        assert items[0]["label"] == "5000 - HOA Dues"
        assert items[0]["annual_budget"] == 12000.0


class TestSourceColumnAuditField:
    def test_real_match_records_annual_budget(self) -> None:
        rows = [
            ["Income", None, None],
            [None, "HOA Dues", 12000.0],
        ]
        col_indices = {
            "annual_budget": 2,
            "_detection_tier": 1,
            "_real_matched_keys": ["annual_budget"],
        }
        items = parse_rows_with_sections(rows, col_indices)
        assert items[0]["source_column"] == "annual_budget"

    def test_fallback_only_marks_source_as_fallback(self) -> None:
        rows = [
            ["Income", None, None],
            [None, "HOA Dues", 12000.0],
        ]
        col_indices = {
            "annual_budget": 2,
            "_detection_tier": 3,
            "_real_matched_keys": [],
        }
        items = parse_rows_with_sections(rows, col_indices)
        assert items[0]["source_column"] == "annual_budget (fallback)"

    def test_legacy_col_indices_without_real_matched_marks_fallback(self) -> None:
        # Backward-compat: existing call sites passing _FALLBACK_COLUMNS
        # directly (no _real_matched_keys) get the fallback marker.
        rows = [
            ["Income", None, None],
            [None, "HOA Dues", 12000.0],
        ]
        col_indices = {"annual_budget": 2}  # no _real_matched_keys
        items = parse_rows_with_sections(rows, col_indices)
        assert items[0]["source_column"] == "annual_budget (fallback)"
