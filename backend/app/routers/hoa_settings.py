"""GET / PUT /hoa/{hoa_id}/settings/disclosure for the disclosure-package config."""
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..ai_implementation.db.models import DisclosurePackageJob, Property
from ..auth.dependencies import get_current_user
from ..services import (
    boilerplate_variables,
    hoa_boilerplate,  # REFERENCE_MAX_BYTES only; the slot API is retired
    hoa_boilerplate_reference_storage,
    hoa_logo_storage,
    hoa_settings_service,
    narrative_content,
    signature_storage,
)

router = APIRouter(prefix="/hoa", tags=["HOA Settings"])


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "property_id": row.property_id,
        "management_company": row.management_company,
        "management_company_address": row.management_company_address,
        "management_company_phone": row.management_company_phone,
        "management_company_fax": row.management_company_fax,
        "management_company_web": row.management_company_web,
        "cpa_firm_name": row.cpa_firm_name,
        "cpa_firm_address": row.cpa_firm_address,
        "reserve_study_expert_name": row.reserve_study_expert_name,
        "reserve_study_date": row.reserve_study_date,
        "reserve_cash_balance_eoy_prior": row.reserve_cash_balance_eoy_prior,
        "fund_balance_boy_operations": row.fund_balance_boy_operations,
        "monthly_assessment_per_unit_prior": row.monthly_assessment_per_unit_prior,
        "interest_rate_after_tax": row.interest_rate_after_tax,
        "replacement_cost_increase_rate": row.replacement_cost_increase_rate,
        "assessment_increase_schedule_json": row.assessment_increase_schedule_json,
        "letter_signed_by": row.letter_signed_by,
        # Priority-A disclosure inputs (drifting-puzzling-grove)
        "approved_monthly_assessment_per_unit": row.approved_monthly_assessment_per_unit,
        "financial_packet_archetype": row.financial_packet_archetype or "dual-fund",
        "reserve_interest_income_override": row.reserve_interest_income_override,
        "income_tax_provision_override": row.income_tax_provision_override,
        "reserve_funding_source": row.reserve_funding_source or "reserve_study_provision",
        "reserve_funding_manual_amount": row.reserve_funding_manual_amount,
        "special_assessments_json": row.special_assessments_json or "[]",
        "additional_assessments_needed_json": row.additional_assessments_needed_json or "[]",
        "outstanding_loan_json": row.outstanding_loan_json,
        # Phase 1 boilerplate-gap fields (drifting-puzzling-grove)
        "letter_date": row.letter_date,
        "letter_signed_by_title": row.letter_signed_by_title,
        "accountant_report_date": row.accountant_report_date,
        "reserve_funding_plan_date": row.reserve_funding_plan_date,
        "hoa_state": row.hoa_state or "CA",
        "hoa_entity_type": row.hoa_entity_type,
        "hoa_incorporation_year": row.hoa_incorporation_year,
        # 30-year reserve funding study (drifting-puzzling-grove rebuild)
        "replacement_fund_monthly_assessment_per_unit": row.replacement_fund_monthly_assessment_per_unit,
        "board_deferrals_json": row.board_deferrals_json or "[]",
        "has_logo": hoa_logo_storage.hoa_logo_exists(row.logo_filename),
        "has_signature": signature_storage.signature_exists(
            getattr(row, "signature_filename", None)
        ),
        "letterhead_logo_mode": (
            getattr(row, "letterhead_logo_mode", None) or "logo_and_text"
        ),
    }


@router.get("/{hoa_id}/settings/disclosure")
async def get_disclosure_settings(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    if not session.query(Property).filter_by(id=hoa_id).one_or_none():
        raise HTTPException(status_code=404, detail=f"HOA not found: {hoa_id}")
    row = hoa_settings_service.get_or_create(session, hoa_id=hoa_id)
    return _row_to_dict(row)


@router.put("/{hoa_id}/settings/disclosure")
async def put_disclosure_settings(
    hoa_id: int,
    payload: dict = Body(...),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    if not session.query(Property).filter_by(id=hoa_id).one_or_none():
        raise HTTPException(status_code=404, detail=f"HOA not found: {hoa_id}")
    try:
        row = hoa_settings_service.update(session, hoa_id=hoa_id, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _row_to_dict(row)


@router.get("/{hoa_id}/assessment/special-pools")
async def list_special_assessment_pools_endpoint(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """Special-assessment pools of the HOA's approved setup, for the Settings
    §5570 section (classified by pool_kind, never by name)."""
    if not session.query(Property).filter_by(id=hoa_id).one_or_none():
        raise HTTPException(status_code=404, detail=f"HOA not found: {hoa_id}")
    from ..disclosure_package.service import list_special_assessment_pools
    return {"pools": list_special_assessment_pools(session, hoa_id=hoa_id)}


@router.post("/{hoa_id}/assessment/special-preview")
async def preview_special_assessment_endpoint(
    hoa_id: int,
    payload: dict = Body(...),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """Per-unit allocation table for one special-assessment pool, computed by the
    same matrix builder the render uses (reflects the saved total)."""
    if not session.query(Property).filter_by(id=hoa_id).one_or_none():
        raise HTTPException(status_code=404, detail=f"HOA not found: {hoa_id}")
    pool_key = payload.get("pool_key")
    if not pool_key:
        raise HTTPException(status_code=400, detail="pool_key is required")
    fiscal_year = payload.get("fiscal_year")
    try:
        fiscal_year = int(fiscal_year)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="fiscal_year (int) is required")
    from ..disclosure_package.service import preview_special_assessment_allocation
    return preview_special_assessment_allocation(
        session, hoa_id=hoa_id, fiscal_year=fiscal_year, pool_key=str(pool_key)
    )


@router.post("/{hoa_id}/settings/logo")
async def upload_hoa_logo(
    hoa_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    if not session.query(Property).filter_by(id=hoa_id).one_or_none():
        raise HTTPException(status_code=404, detail=f"HOA not found: {hoa_id}")
    row = hoa_settings_service.get_or_create(session, hoa_id=hoa_id)
    file_bytes = await file.read()
    try:
        relative_path = hoa_logo_storage.save_hoa_logo(
            property_id=hoa_id,
            file_bytes=file_bytes,
            original_filename=file.filename or "logo",
        )
    except hoa_logo_storage.UnsupportedLogoFileType as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    row.logo_filename = relative_path
    session.commit()
    session.refresh(row)
    return _row_to_dict(row)


@router.delete("/{hoa_id}/settings/logo")
async def delete_hoa_logo(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    if not session.query(Property).filter_by(id=hoa_id).one_or_none():
        raise HTTPException(status_code=404, detail=f"HOA not found: {hoa_id}")
    row = hoa_settings_service.get_or_create(session, hoa_id=hoa_id)
    if row.logo_filename:
        hoa_logo_storage.delete_hoa_logo(row.logo_filename)
        row.logo_filename = None
        session.commit()
        session.refresh(row)
    return _row_to_dict(row)


@router.get("/{hoa_id}/settings/logo")
async def get_hoa_logo(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
) -> FileResponse:
    if not session.query(Property).filter_by(id=hoa_id).one_or_none():
        raise HTTPException(status_code=404, detail=f"HOA not found: {hoa_id}")
    row = hoa_settings_service.get_or_create(session, hoa_id=hoa_id)
    if not row.logo_filename or not hoa_logo_storage.hoa_logo_exists(row.logo_filename):
        raise HTTPException(status_code=404, detail="No logo configured for this HOA")
    return FileResponse(path=hoa_logo_storage.hoa_logo_path(row.logo_filename))


@router.post("/{hoa_id}/settings/signature")
async def upload_hoa_signature(
    hoa_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    if not session.query(Property).filter_by(id=hoa_id).one_or_none():
        raise HTTPException(status_code=404, detail=f"HOA not found: {hoa_id}")
    row = hoa_settings_service.get_or_create(session, hoa_id=hoa_id)
    file_bytes = await file.read()
    try:
        relative_path = signature_storage.save_hoa_signature(
            property_id=hoa_id,
            file_bytes=file_bytes,
            original_filename=file.filename or "signature.png",
        )
    except signature_storage.UnsupportedSignatureFileType as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if row.signature_filename and row.signature_filename != relative_path:
        signature_storage.delete_signature(row.signature_filename)
    row.signature_filename = relative_path
    session.commit()
    session.refresh(row)
    return _row_to_dict(row)


@router.delete("/{hoa_id}/settings/signature")
async def delete_hoa_signature(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    if not session.query(Property).filter_by(id=hoa_id).one_or_none():
        raise HTTPException(status_code=404, detail=f"HOA not found: {hoa_id}")
    row = hoa_settings_service.get_or_create(session, hoa_id=hoa_id)
    if row.signature_filename:
        signature_storage.delete_signature(row.signature_filename)
        row.signature_filename = None
        session.commit()
        session.refresh(row)
    return _row_to_dict(row)


@router.get("/{hoa_id}/settings/signature")
async def get_hoa_signature(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
) -> FileResponse:
    if not session.query(Property).filter_by(id=hoa_id).one_or_none():
        raise HTTPException(status_code=404, detail=f"HOA not found: {hoa_id}")
    row = hoa_settings_service.get_or_create(session, hoa_id=hoa_id)
    if not row.signature_filename or not signature_storage.signature_exists(
        row.signature_filename
    ):
        raise HTTPException(status_code=404, detail="No signature configured for this HOA")
    return FileResponse(path=signature_storage.signature_path(row.signature_filename))


def _require_hoa(session: Session, hoa_id: int) -> None:
    if not session.query(Property).filter_by(id=hoa_id).one_or_none():
        raise HTTPException(status_code=404, detail=f"HOA not found: {hoa_id}")


# ── Narrative documents (add-full-document-editor) ──────────────────────────
#
# Replaced the three-slot /settings/boilerplate pair (retired once the
# frontend cut over). Every narrative document is one editable rich-text
# body, resolved HOA override → firm override → repo baseline; "reset"
# deletes one layer's row.


def _chip_entry(chip_id: str, label: str) -> Dict[str, Any]:
    """Catalog entry plus provenance, for the picker and the chip popover."""
    source = boilerplate_variables.chip_source(chip_id)
    return {
        "id": chip_id,
        "label": label,
        "source": source.kind,
        "source_note": source.note,
        "settings_field": source.field,
        "settings_tab": source.tab,
    }


def _document_payload(session: Session, hoa_id: int) -> Dict[str, Any]:
    return {
        "property_id": hoa_id,
        "documents": narrative_content.documents_for_api(session, hoa_id),
        "variables": [
            _chip_entry(token_id, label)
            for token_id, label in boilerplate_variables.TOKEN_CATALOG.items()
        ],
        "blocks": [
            _chip_entry(block_id, label)
            for block_id, label in boilerplate_variables.BLOCK_CATALOG.items()
        ],
    }


def _require_scope(scope: str, hoa_id: int) -> tuple[str, Optional[int]]:
    """Map the ``scope`` query param onto (scope, scope_id)."""
    if scope == narrative_content.FIRM_SCOPE:
        return narrative_content.FIRM_SCOPE, None
    if scope == narrative_content.HOA_SCOPE:
        return narrative_content.HOA_SCOPE, hoa_id
    raise HTTPException(
        status_code=400, detail="scope must be 'firm' or 'hoa'"
    )


@router.get("/{hoa_id}/documents")
async def list_narrative_documents(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """Every editable document in package order, with computed pages interleaved.

    Computed pages appear as ``kind: "computed"`` placeholder entries (title +
    page-count hint) so the editor can present the report in reading order
    without making the financial schedules editable.
    """
    _require_hoa(session, hoa_id)
    return _document_payload(session, hoa_id)


@router.get("/{hoa_id}/documents/chip-values")
async def get_narrative_chip_values(
    hoa_id: int,
    fiscal_year: Optional[int] = Query(
        None, description="Defaults to the HOA's newest annual package year"
    ),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """What each chip will actually print for this HOA.

    Feeds the editor's chip popover, so it is deliberately best-effort and
    never mirrors generation's failures: an HOA with no active budget still
    gets its name, dates and CPA details back, just without the computed
    figures (see ``chip_preview_values``). Fetched separately from
    ``GET /documents`` so the editor opens at once and the values fill in.
    """
    _require_hoa(session, hoa_id)
    from ..disclosure_package import service as disclosure_service

    return disclosure_service.chip_preview_values(session, hoa_id, fiscal_year)


@router.put("/{hoa_id}/documents")
async def put_narrative_documents(
    hoa_id: int,
    payload: dict = Body(...),
    scope: str = Query("hoa", description="'firm' (all HOAs) or 'hoa' (this one)"),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Save several documents at one scope, atomically.

    Body: ``{ "documents": { "<doc_id>": "<html>", ... } }``.

    Every document is validated before *any* is written, and the whole set
    commits or rolls back together. A per-document loop on the client could
    leave the firm defaults half-rewritten if the third of five saves failed —
    which, at firm scope, is a partial edit visible to every HOA.
    """
    _require_hoa(session, hoa_id)
    write_scope, scope_id = _require_scope(scope, hoa_id)

    raw = payload.get("documents")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="documents object is required")
    if not raw:
        raise HTTPException(status_code=400, detail="documents object is empty")

    try:
        # Validate the whole set first so a bad document later in the batch
        # cannot leave earlier ones committed.
        for document_id, html in raw.items():
            if not isinstance(html, str):
                raise HTTPException(
                    status_code=400, detail=f"html for {document_id!r} must be a string"
                )
            narrative_content.validate_document_html(document_id, html)

        for document_id, html in raw.items():
            narrative_content.save_document(
                session,
                document_id,
                write_scope,
                scope_id,
                html,
                updated_by=(current_user or {}).get("email"),
            )
    except (
        narrative_content.UnknownNarrativeDocument,
        narrative_content.UnknownNarrativeScope,
        narrative_content.MissingRequiredBlock,
        boilerplate_variables.UnknownBoilerplateToken,
    ) as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        session.rollback()
        raise

    session.commit()
    return _document_payload(session, hoa_id)


@router.put("/{hoa_id}/documents/{document_id}")
async def put_narrative_document(
    hoa_id: int,
    document_id: str,
    payload: dict = Body(...),
    scope: str = Query("hoa", description="'firm' (all HOAs) or 'hoa' (this one)"),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    """Save one document at one scope. Body: ``{ "html": "…" }``."""
    _require_hoa(session, hoa_id)
    write_scope, scope_id = _require_scope(scope, hoa_id)

    html = payload.get("html")
    if html is None:
        raise HTTPException(status_code=400, detail="html is required")
    if not isinstance(html, str):
        raise HTTPException(status_code=400, detail="html must be a string")

    try:
        narrative_content.save_document(
            session,
            document_id,
            write_scope,
            scope_id,
            html,
            updated_by=(current_user or {}).get("email"),
        )
    except (
        narrative_content.UnknownNarrativeDocument,
        narrative_content.UnknownNarrativeScope,
        narrative_content.MissingRequiredBlock,
        boilerplate_variables.UnknownBoilerplateToken,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.commit()
    return _document_payload(session, hoa_id)


@router.delete("/{hoa_id}/documents/{document_id}")
async def reset_narrative_document(
    hoa_id: int,
    document_id: str,
    scope: str = Query("hoa", description="'firm' (all HOAs) or 'hoa' (this one)"),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """Reset one document at one scope — it falls back to the layer beneath."""
    _require_hoa(session, hoa_id)
    reset_scope, scope_id = _require_scope(scope, hoa_id)
    try:
        narrative_content.reset_document(session, document_id, reset_scope, scope_id)
    except (
        narrative_content.UnknownNarrativeDocument,
        narrative_content.UnknownNarrativeScope,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _document_payload(session, hoa_id)


@router.get("/{hoa_id}/boilerplate/reference-jobs")
async def list_boilerplate_reference_jobs(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """Completed disclosure jobs for this HOA with package.pdf still on disk.

    HOA-scoped (any authenticated operator on the HOA), not job-creator-only.
    """
    _require_hoa(session, hoa_id)
    jobs = (
        session.query(DisclosurePackageJob)
        .filter(
            DisclosurePackageJob.property_id == hoa_id,
            DisclosurePackageJob.status == "completed",
            DisclosurePackageJob.output_path.isnot(None),
        )
        .order_by(
            DisclosurePackageJob.completed_at.desc(),
            DisclosurePackageJob.id.desc(),
        )
        .limit(50)
        .all()
    )
    items = []
    for job in jobs:
        path = Path(job.output_path) if job.output_path else None
        if path is None or not path.is_file():
            continue
        items.append(
            {
                "job_id": job.id,
                "fiscal_year": job.fiscal_year,
                "completed_at": job.completed_at,
                "annual_package_id": job.annual_package_id,
            }
        )
    return {"jobs": items}


@router.get("/{hoa_id}/boilerplate/reference-pdf")
async def get_boilerplate_reference_pdf(
    hoa_id: int,
    source: str = Query(..., pattern="^(job|upload)$"),
    job_id: Optional[str] = Query(None),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
) -> FileResponse:
    """Stream a reference PDF (HOA-scoped auth)."""
    _require_hoa(session, hoa_id)
    if source == "job":
        if not job_id:
            raise HTTPException(status_code=400, detail="job_id is required when source=job")
        job = (
            session.query(DisclosurePackageJob)
            .filter(
                DisclosurePackageJob.id == job_id,
                DisclosurePackageJob.property_id == hoa_id,
            )
            .one_or_none()
        )
        if job is None or not job.output_path:
            raise HTTPException(status_code=404, detail="Reference job PDF not found")
        path = Path(job.output_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Reference job PDF not found on disk")
        return FileResponse(path=str(path), media_type="application/pdf", filename=path.name)

    row = hoa_settings_service.get_or_create(session, hoa_id=hoa_id)
    if not hoa_boilerplate_reference_storage.reference_exists(row.boilerplate_reference_filename):
        raise HTTPException(status_code=404, detail="No uploaded reference PDF for this HOA")
    path = hoa_boilerplate_reference_storage.reference_path(row.boilerplate_reference_filename)
    return FileResponse(path=str(path), media_type="application/pdf", filename="reference.pdf")


@router.post("/{hoa_id}/boilerplate/reference-pdf")
async def upload_boilerplate_reference_pdf(
    hoa_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """Upload a reference PDF for the workbench (max 25 MiB)."""
    _require_hoa(session, hoa_id)
    payload = await file.read()
    row = hoa_settings_service.get_or_create(session, hoa_id=hoa_id)
    try:
        relative = hoa_boilerplate_reference_storage.save_reference_pdf(
            property_id=hoa_id,
            file_bytes=payload,
            original_filename=file.filename or "reference.pdf",
            max_bytes=hoa_boilerplate.REFERENCE_MAX_BYTES,
        )
    except hoa_boilerplate_reference_storage.UnsupportedReferenceFileType as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except hoa_boilerplate_reference_storage.ReferenceFileTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    row.boilerplate_reference_filename = relative
    session.commit()
    return {"ok": True, "has_reference_upload": True}


@router.delete("/{hoa_id}/boilerplate/reference-pdf")
async def delete_boilerplate_reference_pdf(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    """Remove the uploaded reference PDF for this HOA."""
    _require_hoa(session, hoa_id)
    row = hoa_settings_service.get_or_create(session, hoa_id=hoa_id)
    if row.boilerplate_reference_filename:
        hoa_boilerplate_reference_storage.delete_reference(row.boilerplate_reference_filename)
        row.boilerplate_reference_filename = None
        session.commit()
    return {"ok": True, "has_reference_upload": False}
