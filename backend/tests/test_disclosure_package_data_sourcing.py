"""Smoke test: every number in the rendered PDF traces back to a data source.

Drives the full compile_package pipeline (no render mock) with two
different draft inputs and asserts the rendered PDF text contains the
expected per-unit assessment for each. If the cover letter (or any
template) ever regresses to reading from a hardcoded source, this test
fails.

Skipped automatically when WeasyPrint native deps aren't present
(qpdf_required fixture).
"""
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from app.disclosure_package.compiler import compile_package
from app.disclosure_package.package_specs import OLD_MILL_2026
from app.disclosure_package.schemas import (
    BudgetDraft, HOAMetadata, LineItem, ReserveStudyComponent, ReserveStudySnapshot,
)


def _draft_with_assessment(amount: Decimal) -> BudgetDraft:
    return BudgetDraft(line_items=[
        LineItem(label="40000 - Assessment Income", amount=amount,
                 section="Operating Income > Income", category="operating_revenue", is_revenue=True),
        LineItem(label="50050 - Management Service", amount=Decimal("50000"),
                 section="Administration Expenses", category="administration"),
    ])


def _hoa_metadata(units: int) -> HOAMetadata:
    return HOAMetadata(
        hoa_id=1, name="Test HOA", units=units,
        fiscal_year_start_month=1, fiscal_year_end_month=12,
    )


def _reserve_snapshot() -> ReserveStudySnapshot:
    return ReserveStudySnapshot(
        study_date="2026-01-01",
        components=[ReserveStudyComponent(
            line_item="Roof", useful_life=20, remaining_life=10,
            replacement_cost=Decimal("100000"), year_new=2010,
        )],
    )


@pytest.mark.parametrize("monthly_per_unit_target,units,assessment_total", [
    (Decimal("605.00"), 100, Decimal("726000")),  # 605 * 12 * 100
    (Decimal("750.00"), 200, Decimal("1800000")),
])
def test_changing_assessment_input_changes_rendered_amount(
    tmp_path: Path, qpdf_required,
    monthly_per_unit_target, units, assessment_total,
):
    """Different draft inputs produce different rendered cover-letter amounts."""
    pytest.importorskip(
        "weasyprint",
        reason="WeasyPrint native deps absent; real-render data-sourcing test runs in Docker/CI",
    )
    appendices = tmp_path / "appendices"
    appendices.mkdir()

    result = compile_package(
        spec=OLD_MILL_2026,
        budget_draft=_draft_with_assessment(assessment_total),
        reserve_snapshot=_reserve_snapshot(),
        hoa_metadata=_hoa_metadata(units),
        output_dir=tmp_path / "out",
        appendices_root=appendices,
    )

    text = ""
    with fitz.open(result.output_path) as doc:
        for page in doc:
            text += page.get_text()

    formatted = f"${monthly_per_unit_target:,.2f}"
    assert formatted in text, (
        f"Expected rendered cover letter to contain {formatted!r} when "
        f"draft assessment={assessment_total} and units={units}"
    )
