"""Background job runner + ownership / IDOR enforcement (T-11-01) + path sanitization (T-11-05).

Runs the compile_package pipeline inside a FastAPI BackgroundTask. Reports
status via the disclosure_package_jobs SQLite table.

Plan 11-06 contract:
    * `is_supported_hoa(name)` — REQ-D11-016 phase-scope check used by router.
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
from .adapters import (
    from_budget_history_record,
    from_hoa_record,
    from_reserve_study_extraction,
)
from .compiler import CompileError, compile_package
from .package_specs import SPECS
from .preflight import partition_errors, validate_inputs
from .schemas import PreflightError

logger = logging.getLogger(__name__)

OLD_MILL_LEGAL_NAME = "Old Mill Homeowners Association"
SUPPORTED_HOA_NAMES = {OLD_MILL_LEGAL_NAME}

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
    DB-backed ``package_specs.resolver`` (Phase 1.3 of
    dre-driven-assessment-engine).

    Replaces the old name-string match against ``OLD_MILL_LEGAL_NAME``;
    the resolver inspects the ``properties`` row (name, hoa_code) and
    picks the right manifest. Returns ``None`` when no manifest exists
    for the property — caller surfaces a clear 501 to the router.
    """
    from .package_specs import UnsupportedHOAError, resolve

    try:
        return resolve(property_id, fiscal_year)
    except UnsupportedHOAError:
        return None


def _resolve_spec_for_hoa(hoa_name: str):
    """Backwards-compatible name-string resolver.

    Existing call sites that have only the HOA name (not the property_id)
    can keep working through this shim. New code SHOULD call
    ``_resolve_spec_for_property`` directly.
    """
    if hoa_name == OLD_MILL_LEGAL_NAME:
        return SPECS["old_mill"]
    return None


def is_supported_hoa(hoa_name: str) -> bool:
    """Phase-11 hardcoded scope check, retired by the DRE-driven
    assessment-engine work. Every HOA with a promoted assessment_setup
    + a finalized AnnualPackage is now supported via the universal
    standard package spec. The actual gate is enforced downstream by
    ``preflight.validate_inputs`` (raises a structured error when
    prerequisites are missing); this name-based check always returns
    True so the router doesn't 501 every non-Old-Mill HOA.

    ``hoa_name`` kept on the signature for backward compatibility
    with existing router call sites.
    """
    _ = hoa_name
    return True


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


def _assessment_mapping_category(raw_category: object) -> str:
    category = str(raw_category or "").lower()
    if category == "income":
        return "income"
    if category == "reserve_income":
        return "reserve_income"
    if category in {"reserve", "reserve_expense"}:
        return "reserve_expense"
    return "operating"


def _assessment_mapping_fund_type(category: str) -> str:
    return "reserve" if category in {"reserve_income", "reserve_expense"} else "operating"


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
    ):
        val = getattr(settings_row, field, None)
        if val not in (None, ""):
            overrides[field] = val

    pool_overlay = _resolve_pool_forecast_overlay(session=session, property_id=hoa_id)
    overrides.update(pool_overlay)

    return _PreflightInputBundle(
        spec=spec.model_copy(update={"hoa_id": hoa_id, "fiscal_year": fiscal_year}),
        budget_draft=budget_draft,
        reserve_snapshot=reserve_snapshot,
        hoa_metadata=hoa_metadata,
        overrides=overrides,
    )


def run_preflight(
    session: Session,
    hoa_id: int,
    fiscal_year: int,
) -> tuple[list[PreflightError], list[PreflightError]]:
    """Evaluate disclosure-package readiness without creating a render job.

    Returns ``(blocking, warnings)``. Never raises — resolution failures are
    mapped to blocking PreflightError entries so the caller always gets a list.
    """
    try:
        bundle = _resolve_preflight_inputs(session, hoa_id, fiscal_year)
    except CompileError as exc:
        if exc.errors:
            return exc.errors, []
        field = exc.field_paths[0] if exc.field_paths else "setup"
        return [PreflightError(
            field_path=field,
            message=str(exc),
            severity="blocking",
        )], []
    except LookupError as exc:
        return [PreflightError(
            field_path="budget_draft.line_items",
            message=f"No active budget draft found. Upload and activate a budget before generating.",
            severity="blocking",
            suggested_fix="Go to Budget and upload or activate a budget draft.",
        )], []
    except Exception:
        logger.exception("Unexpected error resolving preflight inputs for HOA %s", hoa_id)
        return [PreflightError(
            field_path="setup",
            message="Could not evaluate readiness due to an unexpected error. Please try again.",
            severity="blocking",
        )], []

    errors = validate_inputs(
        spec=bundle.spec,
        budget_draft=bundle.budget_draft,
        reserve_snapshot=bundle.reserve_snapshot,
        hoa_metadata=bundle.hoa_metadata,
        appendices_root=appendix_dir_for(hoa_id),
        hoa_settings_overrides=bundle.overrides,
    )
    return partition_errors(errors)


def run_render_job(
    job_id: str,
    hoa_id: int,
    fiscal_year: int,
    *,
    session_factory,
    budget_history_service_module: Any = None,
    hoa_service_module: Any = None,
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

        bundle = _resolve_preflight_inputs(
            session,
            hoa_id,
            fiscal_year,
            budget_history_service_module=budget_history_service_module,
        )

        _set_status(session, job_id, stage=DISCLOSURE_STAGE_COMPUTING)

        # Fetch assessment_mode from the property row for the matrix builder.
        property_row = (
            session.query(Property).filter(Property.id == hoa_id).one_or_none()
        )
        assessment_mode = normalize_assessment_mode(
            getattr(property_row, "assessment_mode", None) if property_row else None
        )

        from .compile_inputs import resolve_compile_appendix_paths
        from .assessment_schedule_matrix import build_matrix_for_assessment_mode

        raw_conn = session.connection().connection
        manifest_paths = resolve_compile_appendix_paths(
            property_id=hoa_id, package_id=None, connection=raw_conn,
        )

        assessment_mapping_counts = _materialize_assessment_mappings_for_budget_draft(
            connection=raw_conn,
            hoa_id=hoa_id,
            budget_draft=bundle.budget_draft,
        )
        logger.info(
            "Materialized assessment mappings for disclosure job %s: %s",
            job_id,
            assessment_mapping_counts,
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

        output_dir = _output_dir_for(hoa_id, fiscal_year, job_id)
        result = compile_package(
            spec=bundle.spec,
            budget_draft=bundle.budget_draft,
            reserve_snapshot=bundle.reserve_snapshot,
            hoa_metadata=bundle.hoa_metadata,
            output_dir=output_dir,
            appendices_root=appendix_dir_for(hoa_id),
            hoa_settings_overrides=bundle.overrides,
            extra_appendix_paths=manifest_paths,
            assessment_matrix=assessment_matrix,
        )

        _set_status(
            session,
            job_id,
            status=DISCLOSURE_JOB_COMPLETED,
            stage=None,
            output_path=str(result.output_path),
            audit_path=str(result.audit_path),
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


__all__ = [
    "OLD_MILL_LEGAL_NAME",
    "SUPPORTED_HOA_NAMES",
    "appendix_dir_for",
    "assert_ownership",
    "create_job",
    "delete_appendix",
    "is_supported_hoa",
    "list_appendices",
    "run_preflight",
    "run_render_job",
    "save_appendix",
    "_output_dir_for",
    "_sanitize_segment",
]
