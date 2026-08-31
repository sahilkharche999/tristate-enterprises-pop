"""Background job runner + ownership / IDOR enforcement (T-11-01) + path sanitization (T-11-05).

Runs the compile_package pipeline inside a FastAPI BackgroundTask. Reports
status via the disclosure_package_jobs SQLite table.

Plan 11-06 contract:
    * `_sanitize_segment(value)` — T-11-05 path-traversal mitigation.
    * `_output_dir_for(...)` — composes per-job dir under BUDGET_STORAGE_ROOT.
    * `assert_ownership(...)` — T-11-01 IDOR mitigation; LookupError → 404.
    * `create_job(...)` — T-11-06 SELECT-then-INSERT lock to prevent
      concurrent regenerate races.
    * `run_render_job(...)` — BackgroundTask entry point. Never raises;
      status of failure is recorded in the row.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..assessment_mode import normalize_assessment_mode
from ..ai_implementation.db.models import (
    DISCLOSURE_JOB_COMPLETED,
    DISCLOSURE_JOB_FAILED,
    DISCLOSURE_JOB_PENDING,
    DISCLOSURE_JOB_RUNNING,
    DISCLOSURE_STAGE_COMPUTING,
    DISCLOSURE_STAGE_VALIDATING,
    DisclosurePackageJob,
    Property,
)
from ..config import settings
from ..services.assessment_budget_mapping_rule_service import (
    materialize_budget_line_pool_mappings,
    select_assessment_mapping_amount,
)
from ..services.assessment_mapping_category import (
    _assessment_mapping_category,
    _assessment_mapping_fund_type,
)
from .adapters import (
    from_budget_history_record,
    from_hoa_record,
    from_reserve_study_extraction,
)
from .compiler import CompileError, _compute_all, compile_package
from .preflight import partition_errors, validate_inputs
from .appendix_storage import appendix_file_exists, appendix_file_path
from .schemas import (
    BudgetDraft,
    HOAMetadata,
    PreflightError,
    ReserveStudySnapshot,
)

logger = logging.getLogger(__name__)

# T-11-05: only ASCII alphanum, underscore, hyphen are allowed in any path
# segment derived from user-supplied or DB-supplied identifiers. This rejects
# `..`, `/`, `\`, NUL, and every other separator before path-join time.
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _sanitize_segment(value: str) -> str:
    """Reject any path segment containing path-traversal or slashes (T-11-05).

    Used for hoa_id, fiscal_year, and job_id before joining to BUDGET_STORAGE_ROOT.
    """
    if not isinstance(value, str) or not _SAFE_SEGMENT_RE.match(value):
        raise ValueError(f"Unsafe path segment rejected: {value!r}")
    return value


def _output_dir_for(hoa_id: int, fiscal_year: int, job_id: str) -> Path:
    """Build the per-job output dir under BUDGET_STORAGE_ROOT.

    Example:
        /app/app/ai_implementation/data/budget-storage/disclosure-packages/1/2026/<uuid>/
    """
    root = Path(settings.BUDGET_STORAGE_ROOT)
    safe_hoa = _sanitize_segment(str(hoa_id))
    safe_fy = _sanitize_segment(str(fiscal_year))
    safe_job = _sanitize_segment(str(job_id))
    return root / "disclosure-packages" / safe_hoa / safe_fy / safe_job


def appendix_dir_for(hoa_id: int) -> Path:
    """Per-HOA static-appendix upload dir.

    User-uploaded PDFs land here and the compiler picks them up at
    generate time (sorted by filename). Files survive across jobs and
    fiscal years — they are configuration, not job output.
    """
    root = Path(settings.BUDGET_STORAGE_ROOT)
    safe_hoa = _sanitize_segment(str(hoa_id))
    return root / "disclosure-package-appendices" / safe_hoa


# Characters allowed in a sanitized appendix filename. Anything outside this
# set is replaced with an underscore so realistic operator uploads
# ("ADR Disclosure.pdf", "Pool Rules (2026).pdf") land safely. Path
# separators, control bytes, and shell metacharacters are NOT in this set
# (T-11-05 mitigation).
_APPENDIX_SAFE_CHAR_RE = re.compile(r"[^A-Za-z0-9._\- ()]")


def _line_item_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _assessment_revenue_for_budget_draft(budget_draft: Any) -> Decimal:
    total = Decimal("0")
    for item in getattr(budget_draft, "line_items", []) or []:
        label = str(_line_item_value(item, "label", "") or "")
        category = str(_line_item_value(item, "category", "") or "").lower()
        is_revenue = bool(_line_item_value(item, "is_revenue", False)) or category in {"income", "reserve_income"}
        if not is_revenue or "assessment" not in label.lower():
            continue
        amount, _source_column_used = select_assessment_mapping_amount(
            {
                "assessment_mapping_amount": _line_item_value(item, "assessment_mapping_amount"),
                "source_column_used": _line_item_value(item, "source_column_used"),
                "proposed_amount": _line_item_value(item, "proposed_amount"),
                "proposedAmount": _line_item_value(item, "proposedAmount"),
                "annual_budget": _line_item_value(item, "annual_budget"),
                "projection": _line_item_value(item, "projection"),
                "amount": _line_item_value(item, "amount"),
            }
        )
        if amount is not None:
            total += amount
    return total


def _sanitize_appendix_filename(filename: str) -> str:
    """Coerce an uploaded filename into a path-safe basename.

    Strips any directory component, replaces any character outside the
    allow-list with an underscore, and requires a .pdf suffix. Returns
    the sanitized basename.
    """
    if not isinstance(filename, str):
        raise ValueError("Filename is required")
    base = Path(filename).name.strip()
    if not base or base in (".", ".."):
        raise ValueError(f"Invalid filename: {filename!r}")
    if not base.lower().endswith(".pdf"):
        raise ValueError("Only .pdf uploads are accepted")
    sanitized = _APPENDIX_SAFE_CHAR_RE.sub("_", base)
    # Cap length to keep paths within filesystem limits. Truncate before
    # the suffix so the .pdf extension is preserved.
    if len(sanitized) > 128:
        stem, _, ext = sanitized.rpartition(".")
        sanitized = stem[: 128 - len(ext) - 1] + "." + ext
    return sanitized


def list_appendices(hoa_id: int) -> list[dict]:
    """Return uploaded appendices for an HOA, sorted by filename.

    Each entry: {filename, size_bytes, uploaded_at} (ISO8601 mtime).
    """
    directory = appendix_dir_for(hoa_id)
    if not directory.is_dir():
        return []
    entries: list[dict] = []
    for path in sorted(directory.glob("*.pdf")):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append({
            "filename": path.name,
            "size_bytes": stat.st_size,
            "uploaded_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
        })
    return entries


def save_appendix(hoa_id: int, *, filename: str, content: bytes) -> dict:
    """Persist an uploaded appendix PDF under the per-HOA dir.

    Overwrites if a file of the same sanitized name already exists.
    Returns the same shape as list_appendices() entries.
    """
    base = _sanitize_appendix_filename(filename)
    if not content:
        raise ValueError("Empty file rejected")
    # Lightweight sniff: PDF files start with %PDF-.
    if not content.startswith(b"%PDF-"):
        raise ValueError("File does not appear to be a PDF (missing %PDF- header)")
    directory = appendix_dir_for(hoa_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / base
    target.write_bytes(content)
    stat = target.stat()
    return {
        "filename": base,
        "size_bytes": stat.st_size,
        "uploaded_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
    }


def delete_appendix(hoa_id: int, filename: str) -> bool:
    """Delete a previously uploaded appendix. Returns True if removed."""
    base = _sanitize_appendix_filename(filename)
    target = appendix_dir_for(hoa_id) / base
    if not target.exists():
        return False
    target.unlink()
    return True


def _resolve_spec_for_property(property_id: int, fiscal_year: int):
    """Resolve the PackageSpec for ``(property_id, fiscal_year)`` via the
    DB-backed ``package_specs.resolver``.

    Returns ``None`` when the property row is missing.
    """
    from .package_specs import UnsupportedHOAError, resolve

    try:
        return resolve(property_id, fiscal_year)
    except UnsupportedHOAError:
        return None


def _user_id_from(current_user: dict) -> Optional[int]:
    """Read the integer user id from the auth-dep dict, regardless of key name."""
    raw = current_user.get("id") if isinstance(current_user, dict) else None
    if raw is None and isinstance(current_user, dict):
        raw = current_user.get("user_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def assert_ownership(
    session: Session,
    *,
    job_id: str,
    current_user: dict,
) -> DisclosurePackageJob:
    """T-11-01 IDOR mitigation. Raise LookupError if job belongs to another user.

    Returns the job row on success; raises LookupError otherwise. The router
    maps LookupError to 404 (not 403) — denying existence is preferable for
    IDOR per OWASP ASVS L1.
    """
    job = (
        session.query(DisclosurePackageJob)
        .filter(DisclosurePackageJob.id == job_id)
        .one_or_none()
    )
    if job is None:
        raise LookupError(f"Job not found: {job_id}")
    user_id = _user_id_from(current_user)
    if (
        job.created_by_user_id is not None
        and user_id is not None
        and job.created_by_user_id != user_id
    ):
        raise LookupError(f"Job not found: {job_id}")  # intentionally 404, not 403
    return job


def create_job(
    session: Session,
    *,
    hoa_id: int,
    fiscal_year: int,
    current_user: dict,
    annual_package_id: Optional[int] = None,
) -> DisclosurePackageJob:
    """Insert a new disclosure_package_jobs row in 'pending' state. Concurrent-call safe.

    T-11-06 mitigation: SELECT-then-INSERT in a transaction. If another
    pending/running job for the same (property_id, fiscal_year) exists, raise
    ValueError (router maps to 409). NOT a perfect lock per single-user MVP
    assumption; race window between SELECT and INSERT is acceptable.
    """
    existing = (
        session.query(DisclosurePackageJob)
        .filter(
            DisclosurePackageJob.property_id == hoa_id,
            DisclosurePackageJob.fiscal_year == fiscal_year,
            DisclosurePackageJob.status.in_(
                [DISCLOSURE_JOB_PENDING, DISCLOSURE_JOB_RUNNING]
            ),
        )
        .one_or_none()
    )
    if existing is not None:
        raise ValueError(
            f"A job is already in progress for HOA {hoa_id} "
            f"fiscal year {fiscal_year}: {existing.id}"
        )
    job_id = str(uuid.uuid4())
    user_id = _user_id_from(current_user)
    job = DisclosurePackageJob(
        id=job_id,
        property_id=hoa_id,
        fiscal_year=fiscal_year,
        status=DISCLOSURE_JOB_PENDING,
        created_by_user_id=user_id,
        annual_package_id=annual_package_id,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _set_status(
    session: Session,
    job_id: str,
    *,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    error_message: Optional[str] = None,
    output_path: Optional[str] = None,
    audit_path: Optional[str] = None,
) -> None:
    job = (
        session.query(DisclosurePackageJob)
        .filter(DisclosurePackageJob.id == job_id)
        .one_or_none()
    )
    if job is None:
        return
    if status is not None:
        job.status = status
    if stage is not None:
        job.stage = stage
    if error_message is not None:
        job.error_message = error_message
    if output_path is not None:
        job.output_path = output_path
    if audit_path is not None:
        job.audit_path = audit_path
    if status in (DISCLOSURE_JOB_COMPLETED, DISCLOSURE_JOB_FAILED):
        job.completed_at = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
    session.commit()


def _compile_error_status_message(exc: CompileError) -> str:
    if exc.errors:
        parts: list[str] = []
        for e in exc.errors:
            part = e.message
            if e.suggested_fix:
                part = f"{part} {e.suggested_fix}"
            parts.append(part)
        if len(parts) == 1:
            return parts[0]
        return "Preflight blocked compilation: " + "; ".join(parts)
    # No structured errors on this CompileError — return its plain message.
    return str(exc)


def _line_item_to_assessment_mapping_line(item: Any) -> dict[str, Any]:
    label = str(getattr(item, "label", "") or "")
    category = _assessment_mapping_category(getattr(item, "category", None))
    return {
        "label": label,
        "normalized_label": " ".join(label.lower().split()),
        "section": str(getattr(item, "section", None) or category),
        "category": category,
        "fund_type": _assessment_mapping_fund_type(category),
        "account_code": None,
        "amount": getattr(item, "amount", None),
        "active": True,
    }


def _materialize_assessment_mappings_for_budget_draft(
    *,
    connection: Any,
    hoa_id: int,
    budget_draft: Any,
) -> dict[str, int]:
    setup_row = connection.execute(
        """
        SELECT id
          FROM assessment_setups
         WHERE property_id = ?
           AND status = 'approved'
         ORDER BY id DESC
         LIMIT 1
        """,
        (hoa_id,),
    ).fetchone()
    if setup_row is None:
        return {
            "auto_approved": 0,
            "manual_preserved": 0,
            "suggested": 0,
            "conflict": 0,
            "unmatched": 0,
        }

    return materialize_budget_line_pool_mappings(
        property_id=hoa_id,
        assessment_setup_id=int(setup_row[0]),
        budget_lines=[
            _line_item_to_assessment_mapping_line(line)
            for line in budget_draft.line_items
        ],
        connection=connection,
        commit=False,
    )


def _build_reserve_doc_from_draft(
    draft_payload: Any,
    *,
    session: Any = None,
    hoa_id: int | None = None,
) -> Any:
    """Adapt the draft payload's reserve_study_rows into a duck-typed
    `ExtractedReserveStudyDocument`-shaped object.

    Plan 11-06 design call: rather than pulling a separate
    ExtractedReserveStudyDocument row from the DB, we use the
    canonical reserve_study_rows already living on the active draft
    (BudgetDraftPayload.reserve_study_rows). The disclosure_package
    adapter `from_reserve_study_extraction` accepts duck-typed objects
    (RESEARCH Risk #3), so a SimpleNamespace with `study_date` and
    `rows` is enough — no Phase-10 row schema coupling.

    Reserve study date is read from `hoa_settings.reserve_study_date`
    (auto-populated when the operator uploads a reserve study, editable
    via the Disclosure Settings form).
    """
    rows = list(getattr(draft_payload, "reserve_study_rows", []) or [])
    study_date = ""
    if session is not None and hoa_id is not None:
        from ..services import hoa_settings_service as _hoa_settings_service
        settings_row = _hoa_settings_service.get_or_create(session, hoa_id=hoa_id)
        study_date = getattr(settings_row, "reserve_study_date", None) or ""
    return SimpleNamespace(study_date=study_date, rows=rows)


def _resolve_pool_forecast_overlay(
    *,
    session: Session,
    property_id: int,
) -> dict[str, Any]:
    """Return AssessmentSetup-driven overrides for the 30-year forecast.

    Task #185 of dre-driven-assessment-engine: ``escalation_schedule_json``
    and ``starting_monthly_per_unit`` moved from ``hoa_settings`` to
    ``allocation_pools``. When the operator has set either value on any
    pool of the active AssessmentSetup, that value wins over the
    deprecated hoa_settings column. The first pool (by display_order)
    that supplies each value claims it — fits the current convention
    where the reserve pool is the only one carrying forecast inputs.

    Returns an empty dict when no active setup exists or no pool carries
    forecast values; the caller's hoa_settings reads remain authoritative.
    """
    overlay: dict[str, Any] = {}
    raw_conn = session.connection().connection
    row = raw_conn.execute(
        "SELECT id FROM assessment_setups "
        "WHERE property_id = ? AND status = 'approved' "
        "ORDER BY id DESC LIMIT 1",
        (property_id,),
    ).fetchone()
    if row is None:
        return overlay
    setup_id = row[0]
    pools = raw_conn.execute(
        "SELECT escalation_schedule_json, starting_monthly_per_unit "
        "FROM allocation_pools WHERE assessment_setup_id = ? "
        "ORDER BY display_order, id",
        (setup_id,),
    ).fetchall()
    for schedule, monthly in pools:
        if (
            schedule not in (None, "", "[]")
            and "assessment_increase_schedule_json" not in overlay
        ):
            overlay["assessment_increase_schedule_json"] = schedule
        if (
            monthly is not None
            and "replacement_fund_monthly_assessment_per_unit" not in overlay
        ):
            overlay["replacement_fund_monthly_assessment_per_unit"] = monthly
        if {
            "assessment_increase_schedule_json",
            "replacement_fund_monthly_assessment_per_unit",
        } <= overlay.keys():
            break
    return overlay


@dataclass
class _PreflightInputBundle:
    """Typed bundle of resolved inputs shared by the preflight endpoint and the render job."""
    spec: Any
    budget_draft: Any
    reserve_snapshot: Any
    hoa_metadata: Any
    overrides: dict
    # Narrative document bodies, layered HOA → firm → repo baseline
    # (add-full-document-editor). Always carries every registry key, still
    # holding unresolved chips — resolution needs the compute context.
    narrative: dict


def _resolve_preflight_inputs(
    session: Session,
    hoa_id: int,
    fiscal_year: int,
    *,
    budget_history_service_module: Any = None,
) -> _PreflightInputBundle:
    """Assemble the disclosure-package inputs shared by the preflight check and render.

    Raises:
        CompileError: HOA not found, unit count invalid, or no matching spec.
        LookupError: No active budget draft for the HOA.
    """
    if budget_history_service_module is None:
        from ..services import budget_history_service as budget_history_service_module

    property_row = (
        session.query(Property).filter(Property.id == hoa_id).one_or_none()
    )
    if property_row is None:
        raise CompileError(f"HOA not found: {hoa_id}")

    try:
        hoa_metadata = from_hoa_record(property_row)
    except ValueError as exc:
        raise CompileError(
            "Preflight blocked compilation: 1 error(s)",
            errors=[PreflightError(
                field_path="hoa_metadata.units",
                message="HOA unit count is missing or invalid. Go to Settings and enter a positive unit count for this HOA.",
                severity="blocking",
                suggested_fix="Go to Settings and enter a positive unit count for this HOA.",
            )],
        ) from exc

    spec = _resolve_spec_for_property(hoa_id, fiscal_year)
    if spec is None:
        raise CompileError(
            f"HOA not yet supported in Phase 11: {hoa_metadata.name}"
        )

    budget_payload = budget_history_service_module.get_active_draft(session, hoa_id)
    budget_draft = from_budget_history_record(budget_payload)

    reserve_doc = _build_reserve_doc_from_draft(
        budget_payload, session=session, hoa_id=hoa_id
    )
    reserve_snapshot = from_reserve_study_extraction(reserve_doc)

    from ..services import hoa_settings_service as hoa_settings_module

    settings_row = hoa_settings_module.get_or_create(session, hoa_id=hoa_id)
    overrides: dict = {}
    for field in (
        "management_company", "management_company_address",
        "management_company_phone", "management_company_fax", "management_company_web",
        "cpa_firm_name", "cpa_firm_address", "reserve_study_expert_name",
        "reserve_study_date",
        "reserve_cash_balance_eoy_prior", "fund_balance_boy_operations",
        "monthly_assessment_per_unit_prior", "interest_rate_after_tax",
        "replacement_cost_increase_rate", "letter_signed_by",
        "approved_monthly_assessment_per_unit",
        "financial_packet_archetype",
        "reserve_interest_income_override",
        "income_tax_provision_override",
        "reserve_funding_source",
        "reserve_funding_manual_amount",
        "special_assessments_json",
        "additional_assessments_needed_json",
        "outstanding_loan_json",
        "letter_date",
        "letter_signed_by_title",
        "accountant_report_date",
        "reserve_funding_plan_date",
        "hoa_state",
        "hoa_entity_type",
        "hoa_incorporation_year",
        "assessment_increase_schedule_json",
        "replacement_fund_monthly_assessment_per_unit",
        "board_deferrals_json",
        "logo_filename",
        "letterhead_logo_mode",
        "signature_filename",
    ):
        val = getattr(settings_row, field, None)
        if val not in (None, ""):
            overrides[field] = val
    # Always surface letterhead mode (default logo_and_text) so StrictUndefined
    # templates never miss the key even before brownfield migration runs.
    overrides.setdefault(
        "letterhead_logo_mode",
        getattr(settings_row, "letterhead_logo_mode", None) or "logo_and_text",
    )

    pool_overlay = _resolve_pool_forecast_overlay(session=session, property_id=hoa_id)
    overrides.update(pool_overlay)

    from ..services import narrative_content as narrative_content_module

    narrative = narrative_content_module.resolve_all(session, hoa_id)

    return _PreflightInputBundle(
        spec=spec.model_copy(update={"hoa_id": hoa_id, "fiscal_year": fiscal_year}),
        budget_draft=budget_draft,
        reserve_snapshot=reserve_snapshot,
        hoa_metadata=hoa_metadata,
        overrides=overrides,
        narrative=narrative,
    )


def _assessment_mapping_preflight_errors(
    session: Session,
    hoa_id: int,
    fiscal_year: int,
) -> tuple[list[PreflightError], str]:
    """Return mapping-related preflight errors and step status.

    Step status is one of ``done``, ``needs_action``, ``not_required``.
    Fixed mode never blocks solely for missing line-to-pool mappings.
    """
    from app.assessment_mode import ASSESSMENT_MODE_FIXED, normalize_assessment_mode
    from app.services.assessment_budget_mapping_rule_service import (
        build_assessment_mapping_review_blockers,
        build_assessment_mapping_review_rows,
        build_assessment_mapping_review_summary,
        normalize_budget_label,
        select_assessment_mapping_amount,
    )

    conn = session.connection().connection
    prop = conn.execute(
        "SELECT assessment_mode, default_assessment_setup_id FROM properties WHERE id = ?",
        (hoa_id,),
    ).fetchone()
    mode = normalize_assessment_mode(prop[0] if prop else None)
    if mode == ASSESSMENT_MODE_FIXED:
        return [], "not_required"

    setup_id = int(prop[1]) if prop and prop[1] is not None else None
    if setup_id is None:
        setup_row = conn.execute(
            """
            SELECT id FROM assessment_setups
             WHERE property_id = ? AND status = 'approved'
             ORDER BY id DESC LIMIT 1
            """,
            (hoa_id,),
        ).fetchone()
        setup_id = int(setup_row[0]) if setup_row else None
    if setup_id is None:
        return [
            PreflightError(
                field_path="assessment_setup.status",
                message=(
                    "Variable mode requires an approved DRE or CC&R assessment "
                    "setup before the homeowner schedule can render."
                ),
                severity="blocking",
                code="assessment_setup_missing",
                suggested_fix=(
                    "Open Settings → DRE & Review, complete extraction, and "
                    "Approve → Promote to AssessmentSetup."
                ),
            )
        ], "needs_action"

    draft = conn.execute(
        """
        SELECT id, line_items_json FROM budget_drafts
         WHERE property_id = ? AND status = 'active'
         ORDER BY updated_at DESC, id DESC LIMIT 1
        """,
        (hoa_id,),
    ).fetchone()
    if not draft:
        # Budget absence is already a global blocking finding; mapping step waits.
        return [], "needs_action"

    import json as _json

    try:
        raw_lines = _json.loads(draft[1] or "[]")
    except Exception:
        raw_lines = []
    budget_lines: list[dict] = []
    for item in raw_lines:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("line_item_key") or "")
        category = str(item.get("category") or "operating").lower()
        if category == "income":
            cat = "income"
        elif category == "reserve_income":
            cat = "reserve_income"
        elif category in {"reserve", "reserve_expense"}:
            cat = "reserve_expense"
        else:
            cat = "operating"
        fund = "reserve" if cat in {"reserve_income", "reserve_expense"} else "operating"
        amount, source_col = select_assessment_mapping_amount(item)
        account_code = item.get("account_code")
        budget_lines.append(
            {
                "label": label,
                "normalized_label": normalize_budget_label(label),
                "section": str((item.get("raw") or {}).get("section") or cat),
                "category": cat,
                "fund_type": fund,
                "account_code": str(account_code) if account_code not in (None, "") else None,
                "annual_budget": item.get("annual_budget"),
                "proposed_amount": item.get("proposed_amount")
                if item.get("proposed_amount") is not None
                else item.get("proposedAmount"),
                "projection": item.get("projection"),
                "assessment_mapping_amount": float(amount) if amount is not None else None,
                "source_column_used": source_col,
                "amount": float(amount) if amount is not None else None,
                "reserve_group": item.get("reserve_group") or item.get("reserveGroup"),
                "active": not bool(item.get("inactive")),
            }
        )

    try:
        review_rows = build_assessment_mapping_review_rows(
            property_id=hoa_id,
            assessment_setup_id=setup_id,
            budget_lines=budget_lines,
            budget_year=fiscal_year,
            budget_draft_id=int(draft[0]),
            connection=conn,
        )
        summary = build_assessment_mapping_review_summary(review_rows)
        blockers = build_assessment_mapping_review_blockers(
            property_id=hoa_id,
            assessment_setup_id=setup_id,
            review_rows=review_rows,
            connection=conn,
        )
    except Exception:
        logger.exception("assessment mapping preflight failed for HOA %s", hoa_id)
        return [
            PreflightError(
                field_path="assessment_mapping_review",
                message=(
                    "Could not evaluate assessment mapping review. Open mapping "
                    "review and resolve any incomplete assignments."
                ),
                severity="blocking",
                code="assessment_mapping_eval_failed",
                suggested_fix="Open Assessment Mapping Review and re-check assignments.",
            )
        ], "needs_action"

    if not summary.get("final_render_blocked") and not any(blockers.values()):
        return [], "done"

    parts: list[str] = []
    unresolved = summary.get("unresolved_required_rows") or []
    if unresolved:
        sample = ", ".join(str(x) for x in unresolved[:8])
        more = len(unresolved) - min(len(unresolved), 8)
        suffix = f" (+{more} more)" if more > 0 else ""
        parts.append(f"Unresolved required rows: {sample}{suffix}")
    if summary.get("pending_split_total"):
        parts.append(f"Pending split total: {summary.get('pending_split_total')}")
    failures = summary.get("reconciliation_failures") or []
    if failures:
        parts.append("Reconciliation failures: " + ", ".join(str(f) for f in failures))
    for key, labels in (blockers or {}).items():
        if labels:
            parts.append(f"{key}: " + ", ".join(str(x) for x in labels[:6]))

    detail = " ".join(parts) if parts else "Mapping review is incomplete."
    return [
        PreflightError(
            field_path="assessment_mapping_review",
            message=(
                "Assessment mapping review required before final rendering. "
                + detail
            ),
            severity="blocking",
            code="assessment_mapping_blocked",
            suggested_fix=(
                "Open Assessment Mapping Review, assign or exclude every required "
                "budget line, and clear reconciliation blockers."
            ),
        )
    ], "needs_action"


def _attach_fix_links(errors: list[PreflightError], hoa_id: int) -> list[PreflightError]:
    """Return copies of errors with UI deep-link fields populated."""
    out: list[PreflightError] = []
    for err in errors:
        path = err.field_path or ""
        code = err.code or ""
        fix_path: Optional[str] = err.fix_path
        fix_label = err.fix_label or "Fix"
        if not fix_path:
            if (
                "allocation_resolution" in path
                or code in {
                    "allocation_resolution_required",
                    "combined_line_requires_split",
                    "required_category_unmapped",
                    "referenced_schedule_missing",
                    "approval_required",
                    "invalid_factor_set",
                    "slice_reconciliation_failed",
                    "pool_reconciliation_failed",
                }
            ):
                fix_path = f"/hoa/{hoa_id}/assessment-mapping-review"
                fix_label = "Resolve allocation issues"
            elif "assessment_mapping" in path or code.startswith("assessment_mapping"):
                fix_path = f"/hoa/{hoa_id}/assessment-mapping-review"
                fix_label = "Open mapping review"
            elif path.startswith("assessment_setup") or "dre" in path.lower():
                fix_path = (
                    f"/hoa/{hoa_id}/settings?section=dre"
                    f"&returnTo=/hoa/{hoa_id}/disclosure"
                )
                fix_label = "Open DRE setup"
            elif path.startswith("hoa_settings") or path.startswith("reserve_cash"):
                field = path.split(".")[-1] if "." in path else ""
                # Progressive Disclosure Defaults tabs: cash/funding → money
                money_fields = {
                    "reserve_cash_balance_eoy_prior",
                    "reserve_funding_source",
                    "reserve_funding_manual_amount",
                    "financial_packet_archetype",
                    "approved_monthly_assessment_per_unit",
                }
                tab = "money" if not field or field in money_fields else "money"
                fix_path = (
                    f"/hoa/{hoa_id}/settings?section=disclosure&tab={tab}"
                    + (f"&field={field}" if field else "")
                    + f"&returnTo=/hoa/{hoa_id}/disclosure"
                )
                fix_label = "Open disclosure settings"
            elif path.startswith("budget_draft") or path == "budget_draft.line_items":
                fix_path = f"/hoa/{hoa_id}"
                fix_label = "Open budget"
            elif "appendix" in path.lower():
                fix_path = (
                    f"/hoa/{hoa_id}/settings?section=appendices"
                    f"&returnTo=/hoa/{hoa_id}/disclosure"
                )
                fix_label = "Open appendices"
            elif path.startswith("prior_assessment"):
                fix_path = f"/hoa/{hoa_id}/disclosure"
                fix_label = "Open disclosure"
            elif path.startswith("hoa_metadata"):
                fix_path = (
                    f"/hoa/{hoa_id}/settings?section=database"
                    f"&returnTo=/hoa/{hoa_id}/disclosure"
                )
                fix_label = "Open HOA database"
        out.append(
            err.model_copy(update={"fix_path": fix_path, "fix_label": fix_label})
            if fix_path
            else err
        )
    return out


def _build_readiness_steps(
    *,
    hoa_id: int,
    blocking: list[PreflightError],
    warnings: list[PreflightError],
    mapping_step_status: str,
    has_budget: bool,
    has_reserve_components: bool,
) -> list[dict]:
    """Assemble ordered readiness steps for the disclosure workspace UI."""

    def _has_block(prefix: str) -> bool:
        return any((e.field_path or "").startswith(prefix) for e in blocking)

    def _has_warn(prefix: str) -> bool:
        return any((e.field_path or "").startswith(prefix) for e in warnings)

    budget_status = "needs_action" if _has_block("budget_draft") or not has_budget else "done"
    reserve_status = (
        "warning"
        if _has_warn("reserve_study") or (has_budget and not has_reserve_components)
        else ("done" if has_reserve_components else "warning")
    )
    settings_status = (
        "needs_action"
        if any(
            (e.field_path or "").startswith(p)
            for e in blocking
            for p in ("hoa_settings", "reserve_cash", "reserve_funding", "hoa_metadata")
        )
        else (
            "warning"
            if any(
                (e.field_path or "").startswith(p)
                for e in warnings
                for p in ("hoa_settings", "reserve_cash", "reserve_study")
            )
            else "done"
        )
    )
    setup_status = (
        "needs_action"
        if any(
            (e.field_path or "").startswith("assessment_setup")
            or (e.code or "") == "assessment_setup_missing"
            for e in blocking
        )
        else ("not_required" if mapping_step_status == "not_required" else "done")
    )
    # If variable and setup missing, setup is needs_action; mapping may also be.
    if mapping_step_status == "not_required":
        setup_status = "not_required" if setup_status != "needs_action" else setup_status

    steps = [
        {
            "id": "budget_draft",
            "label": "Budget draft",
            "status": budget_status,
            "detail": "Active budget with line items."
            if budget_status == "done"
            else "Upload and activate a budget draft.",
            "fix_path": f"/hoa/{hoa_id}",
            "fix_label": "Open budget",
        },
        {
            "id": "reserve_study",
            "label": "Reserve study",
            "status": reserve_status,
            "detail": "Reserve components attached."
            if reserve_status == "done"
            else "Attach a reserve study for funded reserve disclosures (warning if missing).",
            "fix_path": f"/hoa/{hoa_id}?view=reserve",
            "fix_label": "Open reserve study",
        },
        {
            "id": "disclosure_settings",
            "label": "Disclosure settings",
            "status": settings_status,
            "detail": "Cash, funding, and letter defaults for the PDF.",
            "fix_path": f"/hoa/{hoa_id}/settings?section=disclosure&returnTo=/hoa/{hoa_id}/disclosure",
            "fix_label": "Open disclosure settings",
        },
        {
            "id": "assessment_setup",
            "label": "DRE or assessment setup",
            "status": setup_status,
            "detail": "Approved allocation setup for variable dues."
            if setup_status != "not_required"
            else "Not required in fixed (equal) assessment mode.",
            "fix_path": f"/hoa/{hoa_id}/settings?section=dre&returnTo=/hoa/{hoa_id}/disclosure",
            "fix_label": "Open DRE setup",
        },
        {
            "id": "assessment_mapping",
            "label": "Assessment mapping",
            "status": mapping_step_status
            if mapping_step_status in {"done", "needs_action", "not_required", "warning"}
            else "needs_action",
            "detail": "Budget lines assigned to assessment pools."
            if mapping_step_status == "done"
            else (
                "Not required in fixed assessment mode."
                if mapping_step_status == "not_required"
                else "Assign required lines before generating the owner PDF."
            ),
            "fix_path": f"/hoa/{hoa_id}/assessment-mapping-review",
            "fix_label": "Open mapping review",
        },
        {
            "id": "appendices",
            "label": "Appendices",
            "status": "done",
            "detail": "Optional policy PDFs (insurance, rules). Missing files are skipped at merge.",
            "fix_path": f"/hoa/{hoa_id}/settings?section=appendices&returnTo=/hoa/{hoa_id}/disclosure",
            "fix_label": "Open appendices",
        },
        {
            "id": "annual_package",
            "label": "Annual package lifecycle",
            "status": "done",
            "detail": "Optional for live generate; use finalize when freezing snapshots.",
            "fix_path": f"/hoa/{hoa_id}/settings?section=packages&returnTo=/hoa/{hoa_id}/disclosure",
            "fix_label": "Open packages",
        },
    ]
    return steps


def run_preflight(
    session: Session,
    hoa_id: int,
    fiscal_year: int,
) -> tuple[list[PreflightError], list[PreflightError]]:
    """Evaluate disclosure-package readiness without creating a render job.

    Returns ``(blocking, warnings)``. Never raises — resolution failures are
    mapped to blocking PreflightError entries so the caller always gets a list.

    For readiness steps and fix links, use :func:`run_preflight_detailed`.
    """
    detailed = run_preflight_detailed(session, hoa_id, fiscal_year)
    return detailed["blocking"], detailed["warnings"]


def run_preflight_detailed(
    session: Session,
    hoa_id: int,
    fiscal_year: int,
) -> dict:
    """Full preflight payload: blocking, warnings, and readiness steps."""
    mapping_step_status = "not_required"
    has_budget = False
    has_reserve_components = False
    try:
        bundle = _resolve_preflight_inputs(session, hoa_id, fiscal_year)
        has_budget = bool(bundle.budget_draft and bundle.budget_draft.line_items)
        has_reserve_components = bool(
            bundle.reserve_snapshot and bundle.reserve_snapshot.components
        )
    except CompileError as exc:
        if exc.errors:
            blocking, warnings = partition_errors(exc.errors)
        else:
            field = exc.field_paths[0] if exc.field_paths else "setup"
            blocking, warnings = [PreflightError(
                field_path=field,
                message=str(exc),
                severity="blocking",
            )], []
        _attach_fix_links(blocking + warnings, hoa_id)
        steps = _build_readiness_steps(
            hoa_id=hoa_id,
            blocking=blocking,
            warnings=warnings,
            mapping_step_status="needs_action",
            has_budget=False,
            has_reserve_components=False,
        )
        return {"blocking": blocking, "warnings": warnings, "steps": steps}
    except LookupError:
        blocking = [PreflightError(
            field_path="budget_draft.line_items",
            message="No active budget draft found. Upload and activate a budget before generating.",
            severity="blocking",
            suggested_fix="Go to Budget and upload or activate a budget draft.",
        )]
        _attach_fix_links(blocking, hoa_id)
        steps = _build_readiness_steps(
            hoa_id=hoa_id,
            blocking=blocking,
            warnings=[],
            mapping_step_status="needs_action",
            has_budget=False,
            has_reserve_components=False,
        )
        return {"blocking": blocking, "warnings": [], "steps": steps}
    except Exception:
        logger.exception("Unexpected error resolving preflight inputs for HOA %s", hoa_id)
        blocking = [PreflightError(
            field_path="setup",
            message="Could not evaluate readiness due to an unexpected error. Please try again.",
            severity="blocking",
        )]
        return {"blocking": blocking, "warnings": [], "steps": []}

    errors = validate_inputs(
        spec=bundle.spec,
        budget_draft=bundle.budget_draft,
        reserve_snapshot=bundle.reserve_snapshot,
        hoa_metadata=bundle.hoa_metadata,
        appendices_root=appendix_dir_for(hoa_id),
        hoa_settings_overrides=bundle.overrides,
        narrative=bundle.narrative,
    )
    # C7 gate: unresolved equal-split placeholders on specified_value pools
    # block generation — a synthetic split must never render as DRE data.
    from .preflight import check_specified_value_placeholders

    errors = errors + check_specified_value_placeholders(
        property_id=hoa_id,
        connection=session.connection().connection,
    )
    from .preflight import check_allocation_resolution_readiness

    errors = errors + check_allocation_resolution_readiness(
        property_id=hoa_id,
        connection=session.connection().connection,
    )
    # Soft YoY warning: prior assessment table omitted when no source exists.
    try:
        from .prior_assessment_schedule import prior_status

        status = prior_status(
            session.connection().connection,
            property_id=hoa_id,
            fiscal_year=fiscal_year,
        )
        if status.get("status") == "missing":
            errors.append(PreflightError(
                field_path="prior_assessment_schedule",
                message=status.get("message")
                or "Prior-year assessment schedule is not available.",
                severity="warning",
                suggested_fix=(
                    "Upload last year’s final package on the Prior-year "
                    "assessment schedule card, or finalize that year’s package."
                ),
            ))
    except Exception:
        logger.exception(
            "prior schedule preflight check failed for HOA %s", hoa_id,
        )

    # Zero cash warning (allowed, but often unintentional empty default).
    try:
        cash = bundle.overrides.get("reserve_cash_balance_eoy_prior")
        if cash is not None:
            from decimal import Decimal as _D

            if _D(str(cash)) == 0:
                errors.append(PreflightError(
                    field_path="hoa_settings.reserve_cash_balance_eoy_prior",
                    message=(
                        "Reserve cash balance (end of prior year) is $0. "
                        "Generation is allowed, but percent funded will look empty "
                        "if the association actually holds reserves."
                    ),
                    severity="warning",
                    code="reserve_cash_zero",
                    suggested_fix=(
                        "Confirm cash is intentionally zero in Disclosure Defaults, "
                        "or enter the board’s reserve cash balance."
                    ),
                ))
    except Exception:
        logger.exception("zero-cash preflight warning failed for HOA %s", hoa_id)

    mapping_errors, mapping_step_status = _assessment_mapping_preflight_errors(
        session, hoa_id, fiscal_year
    )
    errors = errors + mapping_errors

    blocking, warnings = partition_errors(errors)
    linked = _attach_fix_links(blocking + warnings, hoa_id)
    blocking = [e for e in linked if e.severity == "blocking"]
    warnings = [e for e in linked if e.severity == "warning"]
    steps = _build_readiness_steps(
        hoa_id=hoa_id,
        blocking=blocking,
        warnings=warnings,
        mapping_step_status=mapping_step_status,
        has_budget=has_budget,
        has_reserve_components=has_reserve_components,
    )
    return {"blocking": blocking, "warnings": warnings, "steps": steps}


def _latest_fiscal_year(session: Session, hoa_id: int) -> int:
    """Default year for chip preview / generate alignment.

    Prefer ``properties.portfolio_year`` (Settings package year) so narrative
    chips match disclosure generate. Fall back to newest annual package, then
    the current calendar year.
    """
    prop = session.connection().connection.execute(
        "SELECT portfolio_year FROM properties WHERE id = ?",
        (hoa_id,),
    ).fetchone()
    if prop and prop[0] is not None:
        try:
            return int(prop[0])
        except (TypeError, ValueError):
            pass
    row = session.connection().connection.execute(
        "SELECT fiscal_year FROM annual_packages WHERE property_id = ? "
        "ORDER BY fiscal_year DESC, id DESC LIMIT 1",
        (hoa_id,),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else datetime.now().year


def chip_preview_values(
    session: Session, hoa_id: int, fiscal_year: Optional[int] = None
) -> dict[str, Any]:
    """Resolved chip values for the narrative editor's chip popover.

    Best-effort by design: this feeds a "what will print here?" popover, not a
    render, so it must never fail the way generation legitimately can. Two
    tiers:

    * **Full** — the compile inputs resolve, so `_compute_all` runs and every
      chip has a real value, money and percentages included.
    * **Degraded** — no active budget draft, no reserve study, or no spec.
      Identity, letter, CPA and management chips still resolve from the HOA and
      its settings; `previewable_values` drops the computed ones rather than
      show ``$0.00``, which `build_var_map` produces for "unknown" and which an
      operator would read as a real figure.

    Returns ``{fiscal_year, computed_available, unavailable_reason, values}``.
    """
    from ..services import boilerplate_variables as bv

    fiscal_year = fiscal_year or _latest_fiscal_year(session, hoa_id)
    today = datetime.now(timezone.utc).strftime("%A %B %-d, %Y")

    property_row = session.query(Property).filter(Property.id == hoa_id).one_or_none()
    if property_row is None:
        raise LookupError(f"HOA not found: {hoa_id}")

    computed: dict[str, Any] = {}
    matrix = None
    reserve_snapshot = None
    static_data = None
    reason: Optional[str] = None

    try:
        bundle = _resolve_preflight_inputs(session, hoa_id, fiscal_year)
    except (CompileError, LookupError) as exc:
        bundle = None
        reason = str(exc)
    except Exception:  # pragma: no cover — defensive; a popover must not 500
        logger.exception("Chip preview: input resolution failed for HOA %s", hoa_id)
        bundle = None
        reason = "Could not read this HOA's budget inputs."

    if bundle is None:
        # Degraded: the settings row alone. Spec static-data defaults are not
        # applied, so a chip the operator has never filled in previews as blank
        # even though the package would print a firm default.
        from ..services import hoa_settings_service as hoa_settings_module

        settings_row = hoa_settings_module.get_or_create(session, hoa_id=hoa_id)
        effective_settings = {
            key: value
            for key, value in vars(settings_row).items()
            if not key.startswith("_") and value not in (None, "")
        }
        hoa_meta: Any = property_row
    else:
        hoa_meta = bundle.hoa_metadata
        static_data = bundle.spec.static_data
        reserve_snapshot = bundle.reserve_snapshot
        effective_settings = dict(bundle.overrides)
        try:
            # `_compute_all` returns a wrapper — {computed, budget_draft,
            # hoa_metadata, reserve_study_snapshot}. `build_var_map` wants the
            # facts themselves; handing it the wrapper resolves every money
            # chip to $0 rather than raising.
            computed = _compute_all(
                spec=bundle.spec,
                budget_draft=bundle.budget_draft,
                reserve_snapshot=bundle.reserve_snapshot,
                hoa_metadata=bundle.hoa_metadata,
                effective_hoa_settings=effective_settings,
            )["computed"]
        except Exception as exc:
            logger.exception("Chip preview: compute failed for HOA %s", hoa_id)
            computed = {}
            reason = f"Could not compute this HOA's figures: {exc}"

    var_map = bv.build_var_map(
        hoa=hoa_meta,
        fiscal_year=fiscal_year,
        hoa_settings=effective_settings,
        computed=computed,
        matrix=matrix,
        static_data=static_data,
        today=today,
        reserve_study_snapshot=reserve_snapshot,
        toc_page_numbers={},
    )
    computed_available = bool(computed)
    return {
        "fiscal_year": fiscal_year,
        "computed_available": computed_available,
        "unavailable_reason": None if computed_available else reason,
        "values": bv.previewable_values(
            var_map,
            computed_available=computed_available,
            # The matrix build needs `_materialize_assessment_mappings_for_
            # budget_draft`, which writes rows — not something a preview GET
            # may do. So the matrix-dependent chips stay unpreviewed; their
            # popover shows the source note instead of a guessed sentence.
            matrix_available=False,
        ),
    }


def _serialize_assessment_setup_snapshot(
    *,
    connection,
    property_id: int,
) -> dict:
    """Dump the property's default assessment setup + children for the
    finalize snapshot (C2). Audit-record shape: the finalized render reads
    the frozen COMPUTED matrix from the compile-context snapshot, not this
    payload — this preserves what the setup rows said at finalize time.
    """
    from app.services.assessment_budget_mapping_rule_service import (
        resolve_active_assessment_setup_id,
    )

    setup_id = resolve_active_assessment_setup_id(
        connection,
        property_id=property_id,
    )
    setup_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(assessment_setups)"
        ).fetchall()
    }
    display_mode_sql = (
        "display_mode" if "display_mode" in setup_columns else "NULL AS display_mode"
    )
    setup_row = (
        connection.execute(
            f"""
            SELECT id, setup_type, {display_mode_sql}, status, approved_at
              FROM assessment_setups
             WHERE id = ?
            """,
            (setup_id,),
        ).fetchone()
        if setup_id is not None
        else None
    )
    if setup_row is None:
        return {}
    setup_id = setup_row[0]

    def _rows(query: str) -> list[dict]:
        cursor = connection.execute(query, (setup_id,))
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    return {
        "setup": {
            "id": setup_row[0],
            "setup_type": setup_row[1],
            "display_mode": setup_row[2],
            "status": setup_row[3],
            "approved_at": setup_row[4],
        },
        "pools": _rows(
            "SELECT * FROM allocation_pools WHERE assessment_setup_id = ? "
            "ORDER BY display_order, id"
        ),
        "groups": _rows(
            "SELECT * FROM assessment_groups WHERE assessment_setup_id = ? "
            "ORDER BY display_order, id"
        ),
        "units": _rows(
            "SELECT * FROM assessment_units WHERE assessment_setup_id = ? "
            "ORDER BY id"
        ),
        "unit_pool_allocations": _rows(
            "SELECT * FROM assessment_unit_pool_allocations "
            "WHERE assessment_setup_id = ? ORDER BY assessment_unit_id, pool_key"
        ),
    }


def assemble_finalize_snapshots(
    session: Session,
    *,
    hoa_id: int,
    fiscal_year: int,
    package_id: int,
) -> dict[str, Any]:
    """Assemble ALL finalize snapshot payloads server-side from canonical
    DB state (C2). The client can no longer influence the frozen record —
    the finalize endpoint ignores any request body and freezes exactly what
    this function returns, which is by construction the same data the
    render pipeline itself reads.

    Returns the five payloads:
    ``assessment_setup``, ``budget``, ``reserve``, ``appendix_manifest``,
    ``compile_context`` ({assessment_matrix, hoa_metadata,
    hoa_settings_overrides, assessment_revenue_annual}).
    """
    from .appendix_manifest import resolve_appendix_manifest
    from .assessment_schedule_matrix import build_matrix_for_assessment_mode

    bundle = _resolve_preflight_inputs(session, hoa_id, fiscal_year)
    # resolve_appendix_manifest / matrix builder take the raw sqlite3
    # connection (same as run_render_job).
    raw_conn = session.connection().connection

    manifest = resolve_appendix_manifest(
        property_id=hoa_id, package_id=package_id, connection=raw_conn,
    )

    property_row = (
        session.query(Property).filter(Property.id == hoa_id).one_or_none()
    )
    assessment_mode = normalize_assessment_mode(
        getattr(property_row, "assessment_mode", None) if property_row else None
    )

    assessment_revenue = _assessment_revenue_for_budget_draft(bundle.budget_draft)
    if assessment_revenue == Decimal("0"):
        monthly_raw = bundle.overrides.get("approved_monthly_assessment_per_unit")
        if monthly_raw not in (None, ""):
            assessment_revenue = (
                Decimal(str(monthly_raw))
                * Decimal(bundle.hoa_metadata.units)
                * Decimal("12")
            ).quantize(Decimal("0.01"))

    assessment_matrix = build_matrix_for_assessment_mode(
        connection=raw_conn,
        property_id=hoa_id,
        fiscal_year=fiscal_year,
        budget_draft=bundle.budget_draft,
        hoa_name=bundle.hoa_metadata.name,
        unit_count=bundle.hoa_metadata.units,
        approved_assessment_revenue_annual=assessment_revenue,
        assessment_mode=assessment_mode,
    )

    from .prior_assessment_schedule import resolve_prior_assessment_matrix

    prior_matrix = resolve_prior_assessment_matrix(
        raw_conn,
        property_id=hoa_id,
        fiscal_year=fiscal_year,
        hoa_name=bundle.hoa_metadata.name,
    )
    compile_context: dict[str, Any] = {
        "assessment_matrix": assessment_matrix.model_dump(mode="json"),
        "hoa_metadata": bundle.hoa_metadata.model_dump(mode="json"),
        "hoa_settings_overrides": bundle.overrides,
        # Freeze the *layered* narrative bodies (chips still unresolved,
        # exactly as the live branch feeds them to the compiler) so a
        # finalized package re-renders byte-equal no matter how firm or
        # HOA content changes afterwards.
        "narrative": bundle.narrative,
        # Frozen so a finalized package re-renders identically on any later
        # date. Notes 1 and 7 print this ("information available as of …").
        "render_date": datetime.now(timezone.utc).strftime("%A %B %-d, %Y"),
        "assessment_revenue_annual": str(assessment_revenue),
        "assessment_mode": assessment_mode,
    }
    if prior_matrix is not None:
        compile_context["prior_assessment_matrix"] = prior_matrix.model_dump(
            mode="json",
        )
    try:
        from app.allocation_resolution.service import freeze_resolution_snapshot
        from app.services.assessment_budget_mapping_rule_service import (
            resolve_active_assessment_setup_id,
        )

        setup_id = resolve_active_assessment_setup_id(
            raw_conn,
            property_id=hoa_id,
        )
        if setup_id is not None:
            compile_context["allocation_resolution"] = freeze_resolution_snapshot(
                raw_conn, assessment_setup_id=int(setup_id)
            )
    except Exception:
        logger.exception("allocation resolution freeze failed for HOA %s", hoa_id)

    return {
        "assessment_setup": _serialize_assessment_setup_snapshot(
            connection=raw_conn, property_id=hoa_id,
        ),
        "budget": bundle.budget_draft.model_dump(mode="json"),
        "reserve": bundle.reserve_snapshot.model_dump(mode="json"),
        "appendix_manifest": [
            entry.model_dump(mode="json") for entry in manifest
        ],
        "compile_context": compile_context,
    }


def run_render_job(
    job_id: str,
    hoa_id: int,
    fiscal_year: int,
    *,
    session_factory,
    budget_history_service_module: Any = None,
    hoa_service_module: Any = None,
    annual_package_id: Optional[int] = None,
) -> None:
    """BackgroundTask entry point. NEVER raises; records failure in the job row.

    Args:
        job_id: uuid4 string PK on disclosure_package_jobs
        hoa_id, fiscal_year: identifiers used to fetch source data
        session_factory: callable() -> Session; injected because
            BackgroundTasks runs after the request session closes
        budget_history_service_module: optional DI for tests; default to
            the real module at call time
        hoa_service_module: optional DI for tests; default at call time
        annual_package_id: optional target package (C1). When it is
            finalized with valid snapshots the compile reads ONLY the
            frozen snapshot columns for snapshot-covered inputs; a
            finalized package with legacy stub snapshots renders live
            WITH a persistent warning instead of failing.
    """
    if budget_history_service_module is None:
        from ..services import budget_history_service as budget_history_service_module
    if hoa_service_module is None:
        from ..services import hoa_service as hoa_service_module  # noqa: F841

    session = session_factory()
    try:
        _set_status(
            session,
            job_id,
            status=DISCLOSURE_JOB_RUNNING,
            stage=DISCLOSURE_STAGE_VALIDATING,
        )

        from .compile_inputs import (
            resolve_compile_appendix_entries,
            should_use_snapshots,
        )
        from .assessment_schedule_matrix import build_matrix_for_assessment_mode
        from .preflight import check_specified_value_placeholders

        raw_conn = session.connection().connection

        # H6: when the caller didn't name a package, resolve the current one
        # for this (hoa, fiscal_year) so the operator's per-package appendix
        # overrides (exclude/reorder/retitle) actually reach the shipped PDF.
        # Highest id wins — matching annual_package_service
        # ._is_latest_for_fiscal_year — so a newer regeneration draft is used
        # over an older finalized package (there is no per-FY uniqueness).
        # Runs BEFORE should_use_snapshots so a resolved finalized package
        # still renders from its frozen snapshots (C1).
        if annual_package_id is None:
            latest_pkg = raw_conn.execute(
                "SELECT id FROM annual_packages "
                "WHERE property_id = ? AND fiscal_year = ? "
                "ORDER BY id DESC LIMIT 1",
                (hoa_id, fiscal_year),
            ).fetchone()
            if latest_pkg is not None:
                annual_package_id = int(latest_pkg[0])

        use_snapshots = annual_package_id is not None and should_use_snapshots(
            package_id=annual_package_id, connection=raw_conn,
        )
        snapshot_warning: Optional[str] = None
        if annual_package_id is not None and not use_snapshots:
            status_row = raw_conn.execute(
                "SELECT status FROM annual_packages WHERE id = ?",
                (annual_package_id,),
            ).fetchone()
            if status_row is not None and status_row[0] == "finalized":
                snapshot_warning = (
                    f"Finalized package {annual_package_id} has no valid frozen "
                    "snapshots (legacy stub finalization) — this render used "
                    "LIVE data. Re-finalize the package to freeze real snapshots."
                )
                logger.warning("job %s: %s", job_id, snapshot_warning)

        if use_snapshots:
            # ── Frozen-snapshot branch (C1) ────────────────────────────
            # NO live reads or writes for snapshot-covered inputs: no
            # preflight-input resolution, no assessment-mapping
            # materialization (it mutates live state), no live appendix
            # resolution, no C7 placeholder gate (the package passed all
            # gates when it was finalized — that state is frozen).
            from .snapshots import load_package_snapshots

            snaps = load_package_snapshots(
                package_id=annual_package_id, connection=raw_conn,
            )
            context = snaps["compile_context"]

            spec = _resolve_spec_for_property(hoa_id, fiscal_year)
            if spec is None:
                raise CompileError(f"No package spec for HOA {hoa_id}")
            budget_draft = BudgetDraft.model_validate(snaps["budget"])
            reserve_snapshot = ReserveStudySnapshot.model_validate(snaps["reserve"])
            hoa_metadata = HOAMetadata.model_validate(context["hoa_metadata"])
            overrides = dict(context.get("hoa_settings_overrides") or {})
            from ..services import narrative_content as narrative_content_module

            # Frozen narrative only — a document absent from an older snapshot
            # falls back to its repo baseline, never to current live content.
            narrative = narrative_content_module.for_render(
                use_snapshots=True,
                frozen=context.get("narrative"),
            )
            # None for packages finalized before render_date was frozen; those
            # keep the pre-existing "renders with today's date" behavior.
            render_date = context.get("render_date")

            from .assessment_schedule_matrix import AssessmentScheduleMatrix
            from .prior_assessment_schedule import resolve_prior_assessment_matrix

            assessment_matrix = AssessmentScheduleMatrix.model_validate(
                context["assessment_matrix"]
            )
            prior_matrix = resolve_prior_assessment_matrix(
                raw_conn,
                property_id=hoa_id,
                fiscal_year=fiscal_year,
                hoa_name=hoa_metadata.name,
                frozen_prior=context.get("prior_assessment_matrix"),
            )

            _set_status(session, job_id, stage=DISCLOSURE_STAGE_COMPUTING)

            # Appendix paths come from the FROZEN manifest. A missing file
            # hard-fails: a finalized package must never silently shrink.
            manifest_paths = []
            manifest_titles = {}
            insurance_appendix_entries: list[tuple] = []
            for entry in snaps["appendix_manifest"] or []:
                file_id = entry["file_id"]
                if not appendix_file_exists(file_id):
                    raise CompileError(
                        "Finalized package appendix is missing from storage: "
                        f"{entry.get('display_title') or file_id!r}. Restore the "
                        "file or re-finalize the package with a corrected "
                        "appendix set."
                    )
                path = appendix_file_path(file_id)
                title = entry.get("display_title") or path.name
                role = (entry.get("package_role") or "").strip().lower() or None
                if role == "insurance":
                    insurance_appendix_entries.append((path, title))
                else:
                    manifest_paths.append(path)
                    manifest_titles[path.name] = title

            compile_branch_audit = {
                "compile_branch": "snapshot",
                "annual_package_id": annual_package_id,
                "snapshot_finalized_at": snaps.get("finalized_at"),
            }
        else:
            # ── Live branch (pre-change behavior + C7 gate) ────────────
            bundle = _resolve_preflight_inputs(
                session,
                hoa_id,
                fiscal_year,
                budget_history_service_module=budget_history_service_module,
            )
            spec = bundle.spec
            budget_draft = bundle.budget_draft
            reserve_snapshot = bundle.reserve_snapshot
            hoa_metadata = bundle.hoa_metadata
            overrides = bundle.overrides
            narrative = bundle.narrative
            render_date = None  # live render: today's date, as before

            _set_status(session, job_id, stage=DISCLOSURE_STAGE_COMPUTING)

            # _set_status committed the session, which releases the pooled
            # connection captured as `raw_conn` at the top of run_render_job
            # (its DBAPI handle becomes None). Re-acquire a live connection for
            # the raw-SQL work below: the C7 placeholder gate, appendix manifest
            # resolution, assessment-mapping materialization, and matrix build.
            raw_conn = session.connection().connection

            # Fetch assessment_mode from the property row for the matrix builder.
            property_row = (
                session.query(Property).filter(Property.id == hoa_id).one_or_none()
            )
            assessment_mode = normalize_assessment_mode(
                getattr(property_row, "assessment_mode", None) if property_row else None
            )

            # C7 gate: block the render while any specified_value pool still
            # carries auto-generated equal-split placeholder rows — a synthetic
            # split must never render as each unit's DRE-specified amount.
            placeholder_errors = check_specified_value_placeholders(
                property_id=hoa_id, connection=raw_conn,
            )
            if placeholder_errors:
                raise CompileError(
                    f"Preflight blocked compilation: {len(placeholder_errors)} error(s)",
                    errors=placeholder_errors,
                )

            manifest_entries = resolve_compile_appendix_entries(
                property_id=hoa_id,
                package_id=annual_package_id,
                connection=raw_conn,
            )
            manifest_paths = []
            manifest_titles = {}
            insurance_appendix_entries = []
            for path, title, role in manifest_entries:
                if role == "insurance":
                    insurance_appendix_entries.append((path, title))
                else:
                    manifest_paths.append(path)
                    manifest_titles[path.name] = title

            assessment_mapping_counts = _materialize_assessment_mappings_for_budget_draft(
                connection=raw_conn,
                hoa_id=hoa_id,
                budget_draft=budget_draft,
            )
            logger.info(
                "Materialized assessment mappings for disclosure job %s: %s",
                job_id,
                assessment_mapping_counts,
            )

            assessment_revenue = _assessment_revenue_for_budget_draft(budget_draft)
            if assessment_revenue == Decimal("0"):
                monthly_raw = overrides.get("approved_monthly_assessment_per_unit")
                if monthly_raw not in (None, ""):
                    assessment_revenue = (
                        Decimal(str(monthly_raw))
                        * Decimal(hoa_metadata.units)
                        * Decimal("12")
                    ).quantize(Decimal("0.01"))

            from .prior_assessment_schedule import resolve_prior_assessment_matrix

            assessment_matrix = build_matrix_for_assessment_mode(
                connection=raw_conn,
                property_id=hoa_id,
                fiscal_year=fiscal_year,
                budget_draft=budget_draft,
                hoa_name=hoa_metadata.name,
                unit_count=hoa_metadata.units,
                approved_assessment_revenue_annual=assessment_revenue,
                assessment_mode=assessment_mode,
            )
            prior_matrix = resolve_prior_assessment_matrix(
                raw_conn,
                property_id=hoa_id,
                fiscal_year=fiscal_year,
                hoa_name=hoa_metadata.name,
            )

            compile_branch_audit = {
                "compile_branch": "live",
                "annual_package_id": annual_package_id,
                "snapshot_warning": snapshot_warning,
            }

        output_dir = _output_dir_for(hoa_id, fiscal_year, job_id)
        result = compile_package(
            spec=spec,
            budget_draft=budget_draft,
            reserve_snapshot=reserve_snapshot,
            hoa_metadata=hoa_metadata,
            output_dir=output_dir,
            appendices_root=appendix_dir_for(hoa_id),
            hoa_settings_overrides=overrides,
            narrative=narrative,
            render_date=render_date,
            extra_appendix_paths=manifest_paths,
            extra_appendix_titles=manifest_titles,
            insurance_appendix_entries=insurance_appendix_entries,
            assessment_matrix=assessment_matrix,
            prior_matrix=prior_matrix,
            audit_extra=compile_branch_audit,
        )

        _set_status(
            session,
            job_id,
            status=DISCLOSURE_JOB_COMPLETED,
            stage=None,
            output_path=str(result.output_path),
            audit_path=str(result.audit_path),
            # Surface the legacy-stub warning on the completed job so the
            # operator sees it in the job status UI, not only in logs.
            error_message=(
                f"WARNING: {snapshot_warning}" if snapshot_warning else None
            ),
        )
    except CompileError as exc:
        error_message = _compile_error_status_message(exc)
        logger.warning("Compile error for job %s: %s", job_id, error_message)
        _set_status(
            session,
            job_id,
            status=DISCLOSURE_JOB_FAILED,
            error_message=error_message,
        )
    except LookupError as exc:
        # raised by _resolve_preflight_inputs when no active budget draft exists
        human_msg = (
            "No active budget draft found. "
            "Upload and activate a budget before generating."
        )
        logger.warning("Missing input for job %s: %s", job_id, exc)
        _set_status(
            session,
            job_id,
            status=DISCLOSURE_JOB_FAILED,
            error_message=human_msg,
        )
    except Exception:  # noqa: BLE001 — BackgroundTasks must never raise
        logger.exception("Unexpected error in render job %s", job_id)
        _set_status(
            session,
            job_id,
            status=DISCLOSURE_JOB_FAILED,
            error_message=(
                "An unexpected error stopped generation. "
                "Please try again or contact support."
            ),
        )
    finally:
        session.close()


def list_special_assessment_pools(
    session: Session, *, hoa_id: int
) -> list[dict[str, Any]]:
    """Special-assessment pools of the HOA's approved setup (by ``pool_kind``),
    for the Settings §5570 section. Resolves the approved setup the same way the
    matrix builder does, so the ``pool_key`` set can't drift. Empty when there is
    no approved setup or no special pools."""
    raw_conn = session.connection().connection
    from app.services.assessment_budget_mapping_rule_service import (
        resolve_active_assessment_setup_id,
    )

    setup_id = resolve_active_assessment_setup_id(
        raw_conn,
        property_id=hoa_id,
    )
    if setup_id is None:
        return []
    rows = raw_conn.execute(
        "SELECT pool_key, pool_name, allocation_method, recipient_scope "
        "FROM allocation_pools WHERE assessment_setup_id = ? AND pool_kind = ? "
        "ORDER BY display_order, id",
        (setup_id, "separately_billed_special_assessment"),
    ).fetchall()
    return [
        {
            "pool_key": r[0],
            "pool_name": r[1],
            "allocation_method": r[2],
            "recipient_scope": r[3],
        }
        for r in rows
    ]


def preview_special_assessment_allocation(
    session: Session, *, hoa_id: int, fiscal_year: int, pool_key: str
) -> dict[str, Any]:
    """Per-unit allocation table for one special-assessment pool, computed by the
    SAME matrix builder the disclosure render uses (no separate allocator, so the
    preview can't diverge from the rendered package). Reflects the currently saved
    special-assessment total. Returns ``{"available": False, "reason": ...}`` when
    there is no approved setup / active draft, rather than raising."""
    from .assessment_schedule_matrix import build_matrix_for_assessment_mode

    try:
        bundle = _resolve_preflight_inputs(session, hoa_id, fiscal_year)
    except (CompileError, LookupError) as exc:
        return {"available": False, "reason": str(exc)}

    raw_conn = session.connection().connection
    property_row = session.query(Property).filter(Property.id == hoa_id).one_or_none()
    assessment_mode = normalize_assessment_mode(
        getattr(property_row, "assessment_mode", None) if property_row else None
    )
    assessment_revenue = _assessment_revenue_for_budget_draft(bundle.budget_draft)

    matrix = build_matrix_for_assessment_mode(
        connection=raw_conn,
        property_id=hoa_id,
        fiscal_year=fiscal_year,
        budget_draft=bundle.budget_draft,
        hoa_name=bundle.hoa_metadata.name,
        unit_count=bundle.hoa_metadata.units,
        approved_assessment_revenue_annual=assessment_revenue,
        assessment_mode=assessment_mode,
    )
    for block in matrix.special_assessment_blocks:
        if getattr(block, "pool_key", None) == pool_key:
            return {
                "available": True,
                "pool_key": pool_key,
                "allocation_method": block.allocation_method,
                "total": float(block.total) if block.total is not None else None,
                "allocations": [
                    {"recipient_label": row.recipient_label, "amount": float(row.amount)}
                    for row in block.allocations
                ],
            }
    return {
        "available": False,
        "reason": (
            "No allocation for this assessment category yet — enter a total or map a budget line, "
            "and make sure the DRE setup is approved."
        ),
    }


__all__ = [
    "appendix_dir_for",
    "assert_ownership",
    "create_job",
    "delete_appendix",
    "list_appendices",
    "list_special_assessment_pools",
    "preview_special_assessment_allocation",
    "run_preflight",
    "run_render_job",
    "save_appendix",
    "_output_dir_for",
    "_sanitize_segment",
]
