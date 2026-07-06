"""Per-HOA settings CRUD. Backs the disclosure-package configuration UI."""
from __future__ import annotations
from typing import Any, Dict
from sqlalchemy.orm import Session
from ..ai_implementation.db.models import HOASettings

# NOTE on due_date format (task 5.1/5.5): the frontend's date-picker
# guarantees any special-assessment entry a user actually edits is written
# as well-formed MM/DD/YYYY — see mmddyyyyToInputValue/inputValueToMmddyyyy
# in HOADisclosureSettingsForm.tsx. There is deliberately NO hard format
# validation here at the API boundary: the frontend's save() re-sends the
# entire settings payload (not a diff) on every save, so a backend
# rejection here would block an operator from saving ANY unrelated
# disclosure-settings change the moment one HOA has a pre-existing special
# assessment whose due_date predates this format (free text or ISO, per
# the original SpecialAssessmentEntry.due_date comment) — a real
# backward-compatibility break caught during implementation, not a
# hypothetical. This matches the same tolerate-legacy-data convention
# already used for the (now-removed) `frequency` field.

_ALLOWED_FIELDS = {
    "management_company", "management_company_address",
    "management_company_phone", "management_company_fax", "management_company_web",
    "cpa_firm_name", "cpa_firm_address", "reserve_study_expert_name",
    "reserve_study_date",
    "reserve_cash_balance_eoy_prior", "fund_balance_boy_operations",
    "monthly_assessment_per_unit_prior", "interest_rate_after_tax",
    "replacement_cost_increase_rate", "assessment_increase_schedule_json",
    "letter_signed_by",
    # Priority-A disclosure inputs (drifting-puzzling-grove)
    "approved_monthly_assessment_per_unit",
    "financial_packet_archetype",
    "reserve_interest_income_override",
    "income_tax_provision_override",
    "reserve_funding_source",
    "reserve_funding_manual_amount",
    "special_assessments_json",
    "additional_assessments_needed_json",
    "outstanding_loan_json",
    # Phase 1 boilerplate-gap fields (drifting-puzzling-grove)
    "letter_date",
    "letter_signed_by_title",
    "accountant_report_date",
    "reserve_funding_plan_date",
    "hoa_state",
    "hoa_entity_type",
    "hoa_incorporation_year",
    # 30-year reserve funding study (drifting-puzzling-grove rebuild)
    "replacement_fund_monthly_assessment_per_unit",
    "board_deferrals_json",
}


def get_or_create(session: Session, *, hoa_id: int) -> HOASettings:
    row = session.query(HOASettings).filter_by(property_id=hoa_id).one_or_none()
    if row is None:
        row = HOASettings(property_id=hoa_id)
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def update(session: Session, *, hoa_id: int, payload: Dict[str, Any]) -> HOASettings:
    row = get_or_create(session, hoa_id=hoa_id)
    for key, value in payload.items():
        if key not in _ALLOWED_FIELDS:
            raise ValueError(f"Unknown field: {key!r}")
        setattr(row, key, value)
    session.commit()
    session.refresh(row)
    return row
