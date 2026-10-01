"""One dated fiscal-year scenario consumed by every disclosure page.

Compile-time value object. Persist inside annual_packages snapshot JSON on
finalize — not a parallel settings table in v1.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .formulas import total_estimated_liability, total_year_replacement_provision
from .reconciliation import (
    ReserveFundingFacts,
    ReserveInterestTaxFacts,
    parse_optional_decimal_setting,
    resolve_reserve_funding_facts,
    resolve_reserve_interest_tax_facts,
)
from .schemas import LineItem, ReserveFundingPlanRow, ReserveStudySnapshot


class ScenarioFact(BaseModel):
    """One money fact with concept, dating, source, and overwrite flag."""

    model_config = ConfigDict(extra="forbid")

    concept: str
    value: Decimal
    fiscal_year: int
    as_of_date: Optional[str] = None
    source_document_id: Optional[str] = None
    source_label: str = ""
    is_overwrite: bool = False
    is_dated: bool = True


class PackageScenario(BaseModel):
    """Immutable approved facts for one (property, fiscal year) compile."""

    model_config = ConfigDict(extra="forbid")

    fiscal_year: int
    adopted_contribution: ScenarioFact
    study_recommended_contribution: Optional[ScenarioFact] = None
    component_annual_provision: Optional[ScenarioFact] = None
    opening_reserve_cash: ScenarioFact
    fully_funded_liability: ScenarioFact
    reserve_interest: ScenarioFact
    increase_brackets: list[dict[str, Any]] = Field(default_factory=list)
    overwrite_reason: Optional[str] = None
    use_study_funding_calendar: bool = False
    funding_facts: ReserveFundingFacts
    interest_tax_facts: ReserveInterestTaxFacts
    blocking_reasons: list[str] = Field(default_factory=list)


def _parse_json_object(value: object) -> dict[str, Any]:
    if value in (None, "", "{}"):
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_json_list(value: object) -> list[Any]:
    if value in (None, "", "[]"):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _normalize_date(value: object) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _year_from_date(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    digits = "".join(ch if ch.isdigit() else " " for ch in value).split()
    if not digits:
        return None
    try:
        year = int(digits[0])
    except ValueError:
        return None
    if 1900 <= year <= 3000:
        return year
    return None


def resolve_opening_reserve_cash(
    *,
    fiscal_year: int,
    settings: Mapping[str, Any],
    funding_plan_rows: Sequence[ReserveFundingPlanRow],
) -> ScenarioFact:
    """Resolve opening reserve cash for the package year.

    Precedence: per-year map → dated legacy field → matching study opening
    balance → undated leftover (not dated; caller must block issuance).
    """
    year_map = _parse_json_object(settings.get("reserve_cash_by_fiscal_year_json"))
    as_of = _normalize_date(settings.get("reserve_cash_as_of_date"))
    legacy = parse_optional_decimal_setting(settings.get("reserve_cash_balance_eoy_prior"))

    mapped = year_map.get(str(fiscal_year))
    if mapped is None:
        mapped = year_map.get(fiscal_year)
    mapped_amount = parse_optional_decimal_setting(mapped)
    if mapped_amount is not None:
        return ScenarioFact(
            concept="opening_reserve_cash",
            value=mapped_amount,
            fiscal_year=fiscal_year,
            as_of_date=as_of or f"{fiscal_year}-01-01",
            source_label="reserve cash by fiscal year",
            is_dated=True,
        )

    as_of_year = _year_from_date(as_of)
    if as_of_year is not None and legacy is not None:
        if as_of_year in {fiscal_year, fiscal_year - 1}:
            return ScenarioFact(
                concept="opening_reserve_cash",
                value=legacy,
                fiscal_year=fiscal_year,
                as_of_date=as_of,
                source_label="dated reserve cash setting",
                is_dated=True,
            )
        return ScenarioFact(
            concept="opening_reserve_cash",
            value=legacy,
            fiscal_year=fiscal_year,
            as_of_date=as_of,
            source_label="year-mismatched reserve cash",
            is_dated=False,
        )

    for row in funding_plan_rows:
        if row.year == fiscal_year and row.beginning_balance is not None:
            if legacy is None:
                return ScenarioFact(
                    concept="opening_reserve_cash",
                    value=Decimal(row.beginning_balance),
                    fiscal_year=fiscal_year,
                    as_of_date=f"{fiscal_year}-01-01",
                    source_label="reserve study funding-plan opening balance",
                    source_document_id="reserve_study",
                    is_dated=True,
                )

    if legacy is not None and legacy != Decimal("0"):
        return ScenarioFact(
            concept="opening_reserve_cash",
            value=legacy,
            fiscal_year=fiscal_year,
            as_of_date=None,
            source_label="undated reserve cash leftover",
            is_dated=False,
        )

    return ScenarioFact(
        concept="opening_reserve_cash",
        value=legacy if legacy is not None else Decimal("0"),
        fiscal_year=fiscal_year,
        as_of_date=as_of,
        source_label="reserve cash not supplied",
        is_dated=legacy == Decimal("0"),
    )


def build_package_scenario(
    *,
    fiscal_year: int,
    units: int,
    budget_line_items: Sequence[LineItem],
    reserve_snapshot: ReserveStudySnapshot,
    settings: Optional[Mapping[str, Any]] = None,
    source_document_id: Optional[str] = None,
) -> PackageScenario:
    """Build the compile-time scenario from draft + settings + extract."""
    settings = dict(settings or {})
    components = reserve_snapshot.components
    funding_rows = reserve_snapshot.funding_plan_rows
    provision = total_year_replacement_provision(components=components)
    liability = total_estimated_liability(components=components)
    provision_override = parse_optional_decimal_setting(
        settings.get("annual_replacement_provision_override")
    )
    if provision_override is not None:
        provision = provision_override.quantize(Decimal("1"))
    liability_override = parse_optional_decimal_setting(
        settings.get("reserve_liability_override")
    )
    if liability_override is not None:
        liability = liability_override.quantize(Decimal("1"))

    funding_facts = resolve_reserve_funding_facts(
        funding_source=settings.get("reserve_funding_source"),
        manual_annual_amount=settings.get("reserve_funding_manual_amount"),
        budget_line_items=budget_line_items,
        reserve_funding_plan_rows=funding_rows,
        component_annual_provision=provision or Decimal("0"),
        units=units,
        fiscal_year=fiscal_year,
    )
    interest_tax_facts = resolve_reserve_interest_tax_facts(
        reserve_interest_income_override=settings.get("reserve_interest_income_override"),
        income_tax_provision_override=settings.get("income_tax_provision_override"),
        budget_line_items=budget_line_items,
        reserve_funding_plan_rows=funding_rows,
        fiscal_year=fiscal_year,
    )
    cash_fact = resolve_opening_reserve_cash(
        fiscal_year=fiscal_year,
        settings=settings,
        funding_plan_rows=funding_rows,
    )

    overwrite_reason = str(settings.get("reserve_funding_overwrite_reason") or "").strip() or None
    budget_amount = funding_facts.budget_annual_contribution
    selected = funding_facts.annual_contribution
    is_overwrite = bool(
        funding_facts.source
        not in {"budget_reserve_contribution", "missing"}
        and budget_amount is not None
        and selected != budget_amount
    ) or funding_facts.source == "manual"

    adopted = ScenarioFact(
        concept="adopted_reserve_contribution",
        value=selected,
        fiscal_year=fiscal_year,
        source_document_id=source_document_id,
        source_label=(
            "operator overwrite of reserve contribution"
            if is_overwrite
            else funding_facts.source_label
        ),
        is_overwrite=is_overwrite,
        is_dated=True,
    )

    study_fact = None
    if funding_facts.study_recommended_annual_contribution is not None:
        study_fact = ScenarioFact(
            concept="study_recommended_contribution",
            value=funding_facts.study_recommended_annual_contribution,
            fiscal_year=fiscal_year,
            source_label="reserve study cash-flow recommendation",
            source_document_id="reserve_study",
        )
    provision_fact = None
    if funding_facts.component_annual_provision is not None:
        provision_fact = ScenarioFact(
            concept="component_annual_provision",
            value=funding_facts.component_annual_provision,
            fiscal_year=fiscal_year,
            source_label="reserve component annual provision",
            source_document_id="reserve_study",
        )

    liability_fact = ScenarioFact(
        concept="fully_funded_liability",
        value=liability,
        fiscal_year=fiscal_year,
        source_label=(
            "operator overwrite of estimated liability"
            if liability_override is not None
            else "sum of component estimated liabilities"
        ),
        source_document_id="reserve_study",
        is_overwrite=liability_override is not None,
    )
    interest_fact = ScenarioFact(
        concept="reserve_interest",
        value=interest_tax_facts.reserve_interest_income,
        fiscal_year=fiscal_year,
        source_label=interest_tax_facts.interest_source,
        is_overwrite=interest_tax_facts.interest_source == "manual_override",
    )

    brackets = _parse_json_list(settings.get("assessment_increase_schedule_json"))
    use_calendar = bool(settings.get("use_study_funding_calendar"))

    blocking: list[str] = []
    if not cash_fact.is_dated and cash_fact.value != Decimal("0"):
        as_of_year = _year_from_date(cash_fact.as_of_date)
        if as_of_year is not None and as_of_year not in {fiscal_year, fiscal_year - 1}:
            blocking.append(
                f"Reserve cash as-of {cash_fact.as_of_date} does not match "
                f"fiscal year {fiscal_year}."
            )
        else:
            blocking.append(
                "Reserve cash is undated. Attach a dated source (study, "
                "financials, or confirmed overwrite) before generating."
            )
    if (
        is_overwrite
        and not overwrite_reason
        and budget_amount is not None
        and selected != budget_amount
    ):
        blocking.append(
            "Selected reserve funding differs from the income-statement "
            "transfer and no overwrite reason was recorded."
        )

    return PackageScenario(
        fiscal_year=fiscal_year,
        adopted_contribution=adopted,
        study_recommended_contribution=study_fact,
        component_annual_provision=provision_fact,
        opening_reserve_cash=cash_fact,
        fully_funded_liability=liability_fact,
        reserve_interest=interest_fact,
        increase_brackets=brackets if isinstance(brackets, list) else [],
        overwrite_reason=overwrite_reason,
        use_study_funding_calendar=use_calendar,
        funding_facts=funding_facts,
        interest_tax_facts=interest_tax_facts,
        blocking_reasons=blocking,
    )
