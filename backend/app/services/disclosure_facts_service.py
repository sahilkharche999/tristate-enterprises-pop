"""Disclosure facts read from an uploaded budget file -> HOA settings.

A fact fills its settings field only when the field is unset, so an
operator's own value is never overwritten. Otherwise the fact is kept as a
suggestion the operator can apply in Settings. Every fact found is recorded
with its source (file, page, quote) in ``hoa_settings.disclosure_facts_json``.

Year gate: settings describe the package year (``properties.portfolio_year``,
else the current year, as the disclosure screen assumes). A per-unit
assessment printed for that year fills the current-year field; one printed for
the year before fills the prior-year field. Reserve cash is keyed by year, so a
dated balance always lands under the year it opens.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..ai_implementation.db.models import HOASettings, Property
from . import hoa_settings_service


def _unset(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in ("", "[]", "{}", "null")
    if isinstance(value, (int, float)):
        return value == 0
    return False


def _same(current: Any, found: Any) -> bool:
    if isinstance(found, (list, dict)):
        # JSON settings: compare structure, not text (the form's JSON.stringify
        # and json.dumps differ in spacing and 5000 vs 5000.0).
        try:
            current = json.loads(current) if isinstance(current, str) else current
        except (TypeError, ValueError):
            return False
        return current == found
    if isinstance(current, (int, float)) and isinstance(found, (int, float)):
        # Cents for money; exact for decimal rates (0.018 vs 0.02 differ).
        tolerance = 0.005 if abs(float(found)) >= 1 else 1e-9
        return abs(float(current) - float(found)) <= tolerance
    return " ".join(str(current or "").lower().split()) == " ".join(str(found or "").lower().split())


def _iso(text: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(text or "")[:10])
    except ValueError:
        return None


def _money(number: float) -> str:
    return f"${number:,.2f}"


def _increase_brackets(rows: list[dict[str, Any]], package_year: int) -> list[dict[str, Any]]:
    """[{start_year, end_year, rate_percent}] -> the settings shape {start_year, end_year, rate}."""
    brackets: list[dict[str, Any]] = []
    next_start = package_year + 1
    for row in rows:
        if row.get("rate_percent") is None:
            continue
        start = row.get("start_year") or next_start
        end = row.get("end_year")
        brackets.append({"start_year": start, "end_year": end, "rate": round(float(row["rate_percent"]) / 100.0, 6)})
        next_start = (end or start) + 1
    return brackets


def _candidates(facts: dict[str, Any], package_year: int) -> list[dict[str, Any]]:
    """Settings values the facts support: field, label, value, display, fact, auto."""
    out: list[dict[str, Any]] = []
    budget_year = facts.get("budget_fiscal_year")
    # A package more than a year older than the one being prepared carries
    # stale assumptions (study date, increase plan…): suggest, don't fill.
    # Year-keyed values (per-unit assessments, dated reserve cash) are still
    # placed under their own year.
    stale = bool(budget_year) and budget_year < package_year - 1

    def add(field: str, label: str, value: Any, display: str, fact: Optional[dict], auto: bool = True) -> None:
        if stale and field not in ("reserve_cash_by_fiscal_year_json",):
            auto = False
        out.append({"field": field, "label": label, "value": value, "display": display, "fact": fact or {}, "auto": auto})

    def year_of(fact: dict, default: Optional[int]) -> Optional[int]:
        return fact.get("applies_to_year") or default

    fact = facts.get("monthly_assessment_per_unit")
    if fact and fact.get("number"):
        year = year_of(fact, budget_year)
        if year == package_year:
            add("approved_monthly_assessment_per_unit", "Monthly assessment per unit", fact["number"], _money(fact["number"]), fact)
        elif year == package_year - 1:
            add("monthly_assessment_per_unit_prior", "Prior-year monthly assessment per unit", fact["number"], _money(fact["number"]), fact)
    fact = facts.get("monthly_assessment_per_unit_prior_year")
    if fact and fact.get("number") and year_of(fact, budget_year - 1 if budget_year else None) == package_year - 1:
        add("monthly_assessment_per_unit_prior", "Prior-year monthly assessment per unit", fact["number"], _money(fact["number"]), fact)
    fact = facts.get("reserve_portion_of_monthly_assessment")
    if fact and fact.get("number") and year_of(fact, budget_year) == package_year:
        add("replacement_fund_monthly_assessment_per_unit", "Reserve part of the monthly assessment", fact["number"], _money(fact["number"]), fact)

    fact = facts.get("reserve_balance_start_of_budget_year")
    if fact and fact.get("number") is not None:
        as_of = _iso(fact.get("as_of_date"))
        if as_of and (as_of.month, as_of.day) == (12, 31):
            year = as_of.year + 1
            add("reserve_cash_by_fiscal_year_json", f"Reserve cash at the start of {year}",
                (year, float(fact["number"]), as_of.isoformat()),
                f"{_money(fact['number'])} as of {as_of.isoformat()}", fact)
        else:
            # Undated balances can't be filled (the preflight rejects undated
            # cash); suggest them for the budget year so the hint shows there.
            add("reserve_cash_by_fiscal_year_json", "Reserve cash (no date printed)",
                (budget_year or package_year, float(fact["number"]), None), _money(fact["number"]), fact, auto=False)

    for key, field, label in (
        ("reserve_study_preparer", "reserve_study_expert_name", "Reserve study preparer"),
        ("cpa_firm_name", "cpa_firm_name", "CPA firm"),
    ):
        fact = facts.get(key)
        if fact and fact.get("printed"):
            add(field, label, fact["printed"].strip(), fact["printed"].strip(), fact)
    fact = facts.get("reserve_study_date")
    if fact and (fact.get("as_of_date") or fact.get("printed")):
        from ..disclosure_package.preflight import _parse_reserve_study_date

        # Keep the printed wording ("September 2026") when the preflight can
        # read it — the package prints this field as stored.
        printed = (fact.get("printed") or "").strip()
        value = printed if printed and _parse_reserve_study_date(printed) else (fact.get("as_of_date") or printed)
        add("reserve_study_date", "Reserve study date", value, printed or value, fact)

    brackets = _increase_brackets(facts.get("planned_assessment_increases") or [], package_year)
    if brackets:
        display = "; ".join(
            f"{b['rate'] * 100:g}% {b['start_year']}–{b['end_year'] or 'on'}" for b in brackets
        )
        add("assessment_increase_schedule_json", "Planned assessment increases", brackets, display, None)

    fact = facts.get("outstanding_loan_balance")
    if fact and (fact.get("number") or 0) > 0:
        loan = {"balance": float(fact["number"]), "lender": facts.get("outstanding_loan_lender"),
                "original_amount": None, "interest_rate": None, "payoff_date": None, "purpose": None}
        add("outstanding_loan_json", "Outstanding loan", loan, _money(fact["number"]), fact)

    fact = facts.get("reserve_interest_rate")
    if fact and fact.get("number") is not None:
        basis = facts.get("reserve_interest_rate_basis")
        display = f"{fact['number']:g}%" + (" before tax" if basis == "before_tax" else " after tax" if basis == "after_tax" else "")
        # Never filled automatically: the field is after-tax, has a boot-time
        # default, and packages often print a before-tax rate.
        add("interest_rate_after_tax", "Interest rate on reserves", round(float(fact["number"]) / 100.0, 6), display, fact, auto=False)
    fact = facts.get("percent_funded")
    if fact and fact.get("number") is not None:
        # An override of a computed value: suggest, never fill.
        add("percent_funded_override", "Percent funded", float(fact["number"]), f"{fact['number']:g}%", fact, auto=False)
    return out


def apply_disclosure_facts(
    session: Session,
    *,
    hoa: Property,
    facts: Optional[dict[str, Any]],
    upload_id: int,
    filename: str,
) -> list[dict[str, Any]]:
    """Fill unset settings from ``facts`` and record every finding.

    Returns one summary row per finding:
    ``{field, label, value, status (applied|suggested|matches), page, quote}``.
    """
    if not facts:
        return []
    row: HOASettings = hoa_settings_service.get_or_create(session, hoa_id=hoa.id)
    package_year = hoa.portfolio_year or datetime.now(timezone.utc).year
    try:
        provenance = json.loads(row.disclosure_facts_json or "{}")
    except (TypeError, json.JSONDecodeError):
        provenance = {}
    found_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    summary: list[dict[str, Any]] = []
    changed = False

    for candidate in _candidates(facts, package_year):
        field, value = candidate["field"], candidate["value"]
        key = field
        if field == "reserve_cash_by_fiscal_year_json":
            try:
                mapping = json.loads(row.reserve_cash_by_fiscal_year_json or "{}")
            except (TypeError, json.JSONDecodeError):
                mapping = {}
            year, amount, as_of = value
            key = f"{field}:{year}"
            if str(year) in mapping:
                status = "matches" if _same(mapping[str(year)], amount) else "suggested"
            elif not candidate["auto"]:
                status = "suggested"
            else:
                mapping[str(year)] = amount
                row.reserve_cash_by_fiscal_year_json = json.dumps(mapping)
                # The form writes the year map, the legacy amount and the
                # as-of date together; keep them in step for the package year.
                if year == package_year and _unset(row.reserve_cash_balance_eoy_prior):
                    row.reserve_cash_balance_eoy_prior = amount
                    row.reserve_cash_as_of_date = as_of
                status = "applied"
        else:
            current = getattr(row, field)
            stored = json.dumps(value) if field.endswith("_json") else value
            if _unset(current) and candidate["auto"]:
                setattr(row, field, stored)
                status = "applied"
            elif not _unset(current) and _same(current, stored):
                status = "matches"
            else:
                status = "suggested"
        changed = changed or status == "applied"
        fact = candidate["fact"]
        provenance[key] = {
            "field": field,
            "label": candidate["label"],
            "value": value[1] if field == "reserve_cash_by_fiscal_year_json" else value,
            "display": candidate["display"],
            "status": status,
            "page": fact.get("page"),
            "quote": fact.get("quote"),
            # Lets the form's Use fill a balance's date with its amount.
            "as_of_date": value[2] if field == "reserve_cash_by_fiscal_year_json" else None,
            "upload_id": upload_id,
            "filename": filename,
            "found_at": found_at,
        }
        summary.append({
            "field": field,
            "label": candidate["label"],
            "value": candidate["display"],
            "status": status,
            "page": fact.get("page"),
            "quote": fact.get("quote"),
        })

    row.disclosure_facts_json = json.dumps(provenance)
    if changed:
        row.version_int = (row.version_int or 0) + 1
    session.flush()
    return summary
