"""Fiscal-year / statement-period inference tests.

Former HTTP tests hit deleted `/macros/generate-budget`. Coverage lives on
the shared inference helpers and budget-history pipeline separately.
"""
from app.services.statement_period_inference import (
    infer_growth_factor_from_statement_period,
    select_statement_period_hint,
)


def test_select_statement_period_hint_prefers_date_range_over_report_timestamp():
    text = (
        "Income Statement - Operating Date: 10/21/2025\n"
        "2238 Market Condominium Association Time: 10:26 am\n"
        "08/21/2025 to 09/20/2025 Page: 1\n"
    )

    hint = select_statement_period_hint(text)

    assert hint == "08/21/2025 to 09/20/2025 Page: 1"


def test_infer_growth_factor_from_statement_period_aug_sep_range():
    factor, statement_month, source = infer_growth_factor_from_statement_period(
        "08/21/2025 to 09/20/2025",
        fiscal_year_start_month=1,
    )
    assert statement_month == 9
    assert factor == 12.0 / 9.0
    assert source
