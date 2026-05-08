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
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy.orm import Session

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
from .adapters import (
    from_budget_history_record,
    from_hoa_record,
    from_reserve_study_extraction,
)
from .compiler import CompileError, compile_package
from .package_specs import SPECS

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


# Filenames for uploaded appendices: keep operator-friendly characters
# (letters, digits, underscore, hyphen, dot) and reject anything else.
# Length cap of 128 chars matches BUDGET_STORAGE_ROOT operational limits.
_APPENDIX_FILENAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")


def _sanitize_appendix_filename(filename: str) -> str:
    """Reject path-traversal or non-PDF uploads (T-11-05 family).

    Strips any directory component, requires .pdf suffix, and validates
    the remaining basename against an allow-list regex.
    """
    if not isinstance(filename, str):
        raise ValueError("Filename is required")
    base = Path(filename).name
    if not base or base in (".", ".."):
        raise ValueError(f"Invalid filename: {filename!r}")
    if not base.lower().endswith(".pdf"):
        raise ValueError("Only .pdf uploads are accepted")
    if not _APPENDIX_FILENAME_RE.match(base):
        raise ValueError(
            f"Filename may only contain letters, digits, '.', '_' and '-': {base!r}"
        )
    return base


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


def _resolve_spec_for_hoa(hoa_name: str):
    """Match the HOA name to a known PackageSpec (REQ-D11-016).

    Phase 11 ships only Old Mill; other HOAs get a clear 501 from the router.
    """
    if hoa_name == OLD_MILL_LEGAL_NAME:
        return SPECS["old_mill"]
    return None


def is_supported_hoa(hoa_name: str) -> bool:
    """Phase 11 scope check used by the router (REQ-D11-016)."""
    return hoa_name in SUPPORTED_HOA_NAMES


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


def _build_reserve_doc_from_draft(draft_payload: Any) -> Any:
    """Adapt the draft payload's reserve_study_rows into a duck-typed
    `ExtractedReserveStudyDocument`-shaped object.

    Plan 11-06 design call: rather than pulling a separate
    ExtractedReserveStudyDocument row from the DB, we use the
    canonical reserve_study_rows already living on the active draft
    (BudgetDraftPayload.reserve_study_rows). The disclosure_package
    adapter `from_reserve_study_extraction` accepts duck-typed objects
    (RESEARCH Risk #3), so a SimpleNamespace with `study_date` and
    `rows` is enough — no Phase-10 row schema coupling.
    """
    rows = list(getattr(draft_payload, "reserve_study_rows", []) or [])
    return SimpleNamespace(study_date="", rows=rows)


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

        # Fetch the Property ORM row directly. hoa_service.get_hoa returns
        # a Pydantic HOADetail (which lacks the raw `units`/`name` fields
        # in the right shape for from_hoa_record), so we go to the row.
        property_row = (
            session.query(Property).filter(Property.id == hoa_id).one_or_none()
        )
        if property_row is None:
            raise CompileError(f"HOA not found: {hoa_id}")
        hoa_metadata = from_hoa_record(property_row)

        spec = _resolve_spec_for_hoa(hoa_metadata.name)
        if spec is None:
            raise CompileError(
                f"HOA not yet supported in Phase 11: {hoa_metadata.name}"
            )

        budget_payload = budget_history_service_module.get_active_draft(
            session, hoa_id
        )
        budget_draft = from_budget_history_record(budget_payload)

        reserve_doc = _build_reserve_doc_from_draft(budget_payload)
        reserve_snapshot = from_reserve_study_extraction(reserve_doc)

        _set_status(session, job_id, stage=DISCLOSURE_STAGE_COMPUTING)

        # Load operator-saved disclosure settings (Phase 11 plan 11-08
        # Task 4). Fields left at the defaults / unset are skipped so
        # spec.static_data continues to drive them in compile_package.
        # Local import keeps the module-import graph cycle-free.
        from ..services import hoa_settings_service as hoa_settings_module

        settings_row = hoa_settings_module.get_or_create(session, hoa_id=hoa_id)
        overrides: dict = {}
        for field in (
            "management_company", "management_company_address",
            "management_company_phone", "management_company_fax", "management_company_web",
            "cpa_firm_name", "cpa_firm_address", "reserve_study_expert_name",
            "reserve_cash_balance_eoy_prior", "fund_balance_boy_operations",
            "monthly_assessment_per_unit_prior", "interest_rate_after_tax",
            "replacement_cost_increase_rate", "letter_signed_by",
        ):
            val = getattr(settings_row, field, None)
            if val not in (None, "", 0, 0.0):
                overrides[field] = val

        output_dir = _output_dir_for(hoa_id, fiscal_year, job_id)
        result = compile_package(
            spec=spec.model_copy(
                update={"hoa_id": hoa_id, "fiscal_year": fiscal_year}
            ),
            budget_draft=budget_draft,
            reserve_snapshot=reserve_snapshot,
            hoa_metadata=hoa_metadata,
            output_dir=output_dir,
            appendices_root=appendix_dir_for(hoa_id),
            hoa_settings_overrides=overrides,
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
        logger.warning("Compile error for job %s: %s", job_id, exc)
        _set_status(
            session,
            job_id,
            status=DISCLOSURE_JOB_FAILED,
            error_message=str(exc),
        )
    except LookupError as exc:
        # raised by services.budget_history_service.get_active_draft when
        # no draft exists for the HOA — surface as a clean failure not a 500
        logger.warning("Missing input for job %s: %s", job_id, exc)
        _set_status(
            session,
            job_id,
            status=DISCLOSURE_JOB_FAILED,
            error_message=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 — BackgroundTasks must never raise
        logger.exception("Unexpected error in render job %s", job_id)
        _set_status(
            session,
            job_id,
            status=DISCLOSURE_JOB_FAILED,
            error_message=f"Internal error: {type(exc).__name__}: {exc}",
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
    "run_render_job",
    "save_appendix",
    "_output_dir_for",
    "_sanitize_segment",
]
