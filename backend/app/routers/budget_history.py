"""Protected HOA-scoped budget history endpoints."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..auth.dependencies import get_current_user
from ..models.budget_history import (
    BudgetBundleUploadResponse,
    BudgetDraftCompareOptionsResponse,
    BudgetDraftCompareRequest,
    BudgetDraftCompareResponse,
    BudgetDraftReserveReviewRequest,
    BudgetDraftReserveReviewResponse,
    BudgetDraftPayload,
    BudgetDraftSaveRequest,
    BudgetDraftSaveResponse,
    BudgetReserveStudyApplyResponse,
    BudgetReserveStudySaveRequest,
    BudgetGenerateRequest,
    BudgetGenerateResponse,
    BudgetHistoryResponse,
    BudgetNoteSaveRequest,
    BudgetNoteSaveResponse,
    BudgetUploadResponse,
    BudgetVersionCompareResponse,
    BudgetVersionDetail,
    BudgetVersionMetadataUpdateRequest,
    BudgetVersionMetadataUpdateResponse,
    BudgetVersionReopenResponse,
)
from ..services import budget_history_service

router = APIRouter(tags=["Budget History"])


@router.post("/hoa/{hoa_id}/budget/upload", response_model=BudgetUploadResponse)
async def upload_budget(
    hoa_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetUploadResponse:
    try:
        return budget_history_service.create_upload(
            session,
            hoa_id=hoa_id,
            actor=current_user,
            original_filename=file.filename or "upload.xlsx",
            content_type=file.content_type,
            file_bytes=await file.read(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/hoa/{hoa_id}/budget/upload-bundle", response_model=BudgetBundleUploadResponse)
async def upload_budget_bundle(
    hoa_id: int,
    budget_file: UploadFile = File(...),
    reserve_study_file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetBundleUploadResponse:
    try:
        return budget_history_service.create_upload_bundle(
            session,
            hoa_id=hoa_id,
            actor=current_user,
            budget_filename=budget_file.filename or "budget-upload.xlsx",
            budget_content_type=budget_file.content_type,
            budget_file_bytes=await budget_file.read(),
            reserve_filename=reserve_study_file.filename or "reserve-study.pdf",
            reserve_content_type=reserve_study_file.content_type,
            reserve_file_bytes=await reserve_study_file.read(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/hoa/{hoa_id}/budget/draft")
async def delete_budget_draft(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
):
    try:
        return budget_history_service.delete_active_draft(session, hoa_id, current_user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        if "cannot_delete_draft_with_versions" in str(exc):
            raise HTTPException(
                status_code=409,
                detail="Cannot delete a draft that has generated versions. Use the upload flow to start a new draft instead.",
            )
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/hoa/{hoa_id}/budget/draft", response_model=BudgetDraftPayload)
async def get_budget_draft(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetDraftPayload:
    try:
        return budget_history_service.get_active_draft(session, hoa_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/hoa/{hoa_id}/budget/drafts/{draft_id}", response_model=BudgetDraftPayload)
async def get_budget_draft_by_id(
    hoa_id: int,
    draft_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetDraftPayload:
    try:
        return budget_history_service.get_requested_draft(session, hoa_id, draft_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/hoa/{hoa_id}/budget/drafts/{draft_id}/download-enriched")
async def download_budget_draft_enriched(
    hoa_id: int,
    draft_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> FileResponse:
    try:
        file_path, filename = budget_history_service.record_draft_enriched_download(
            session,
            hoa_id=hoa_id,
            actor=current_user,
            draft_id=draft_id,
        )
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/hoa/{hoa_id}/budget/draft", response_model=BudgetDraftSaveResponse)
async def save_budget_draft(
    hoa_id: int,
    payload: BudgetDraftSaveRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetDraftSaveResponse:
    try:
        draft, timeline_event = budget_history_service.save_draft(
            session,
            hoa_id=hoa_id,
            actor=current_user,
            payload=payload,
        )
        return BudgetDraftSaveResponse(draft=draft, timeline_event=timeline_event)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.patch("/hoa/{hoa_id}/budget/drafts/{draft_id}/reserve-study", response_model=BudgetDraftPayload)
async def save_budget_reserve_study(
    hoa_id: int,
    draft_id: int,
    payload: BudgetReserveStudySaveRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetDraftPayload:
    try:
        return budget_history_service.save_reserve_study_rows(
            session,
            hoa_id=hoa_id,
            draft_id=draft_id,
            actor=current_user,
            payload=payload,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post(
    "/hoa/{hoa_id}/budget/drafts/{draft_id}/reserve-study/apply",
    response_model=BudgetReserveStudyApplyResponse,
)
async def apply_budget_reserve_study(
    hoa_id: int,
    draft_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetReserveStudyApplyResponse:
    try:
        return budget_history_service.apply_reserve_study_to_budget(
            session,
            hoa_id=hoa_id,
            draft_id=draft_id,
            actor=current_user,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/hoa/{hoa_id}/budget/notes", response_model=BudgetNoteSaveResponse)
async def save_budget_note(
    hoa_id: int,
    payload: BudgetNoteSaveRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetNoteSaveResponse:
    try:
        return budget_history_service.save_note(
            session,
            hoa_id=hoa_id,
            actor=current_user,
            payload=payload,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/hoa/{hoa_id}/budget/generate", response_model=BudgetGenerateResponse)
async def generate_budget_version(
    hoa_id: int,
    payload: BudgetGenerateRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetGenerateResponse:
    try:
        return budget_history_service.create_budget_version(
            session,
            hoa_id=hoa_id,
            actor=current_user,
            payload=payload,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/hoa/{hoa_id}/budget/compare/options", response_model=BudgetDraftCompareOptionsResponse)
async def get_budget_draft_compare_options(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetDraftCompareOptionsResponse:
    try:
        return budget_history_service.get_draft_compare_options(session, hoa_id=hoa_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/hoa/{hoa_id}/budget/drafts/{draft_id}/compare", response_model=BudgetDraftCompareResponse)
async def compare_budget_draft_to_version(
    hoa_id: int,
    draft_id: int,
    payload: BudgetDraftCompareRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetDraftCompareResponse:
    try:
        return budget_history_service.compare_draft_to_version(
            session,
            hoa_id=hoa_id,
            draft_id=draft_id,
            baseline_version_id=payload.baseline_version_id,
            changed_only=payload.changed_only,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post(
    "/hoa/{hoa_id}/budget/drafts/{draft_id}/reserve-review",
    response_model=BudgetDraftReserveReviewResponse,
)
async def review_budget_draft_reserves(
    hoa_id: int,
    draft_id: int,
    payload: BudgetDraftReserveReviewRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetDraftReserveReviewResponse:
    try:
        return budget_history_service.review_draft_reserves(
            session,
            hoa_id=hoa_id,
            draft_id=draft_id,
            payload=payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/hoa/{hoa_id}/history", response_model=BudgetHistoryResponse)
async def get_budget_history(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetHistoryResponse:
    try:
        return budget_history_service.get_history(session, hoa_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/hoa/{hoa_id}/versions/compare", response_model=BudgetVersionCompareResponse)
async def compare_budget_versions(
    hoa_id: int,
    left: int,
    right: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetVersionCompareResponse:
    try:
        return budget_history_service.compare_versions(
            session,
            hoa_id=hoa_id,
            left_version_id=left,
            right_version_id=right,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/hoa/{hoa_id}/versions/{version_id}", response_model=BudgetVersionDetail)
async def get_budget_version_detail(
    hoa_id: int,
    version_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetVersionDetail:
    try:
        return budget_history_service.get_version_detail(session, hoa_id, version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/hoa/{hoa_id}/versions/{version_id}/reopen", response_model=BudgetVersionReopenResponse)
async def reopen_budget_version(
    hoa_id: int,
    version_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetVersionReopenResponse:
    try:
        return budget_history_service.reopen_version_as_draft(
            session,
            hoa_id=hoa_id,
            actor=current_user,
            version_id=version_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch("/hoa/{hoa_id}/versions/{version_id}", response_model=BudgetVersionMetadataUpdateResponse)
async def update_budget_version(
    hoa_id: int,
    version_id: int,
    payload: BudgetVersionMetadataUpdateRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> BudgetVersionMetadataUpdateResponse:
    try:
        return budget_history_service.update_version_metadata(
            session,
            hoa_id=hoa_id,
            actor=current_user,
            version_id=version_id,
            payload=payload,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/hoa/{hoa_id}/versions/{version_id}/download")
async def download_budget_version(
    hoa_id: int,
    version_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> FileResponse:
    try:
        file_path, filename = budget_history_service.record_version_download(
            session,
            hoa_id=hoa_id,
            actor=current_user,
            version_id=version_id,
        )
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
