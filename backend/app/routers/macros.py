"""HTTP router exposing macro-like endpoints."""
from __future__ import annotations
import json
import logging
import os
import shutil
import tempfile
from typing import Optional

from fastapi import APIRouter, File, UploadFile, Form, HTTPException, Response, BackgroundTasks
from fastapi.concurrency import run_in_threadpool

from ..config import settings
from ..models.financial_document_extraction import DocumentExtractionFailure
from ..models.schemas import TableResponse, SumResponse, DupsResponse, SimpleResponse
from ..services import macros_service
from ..services.financial_document_router import choose_financial_document_route
from ..services.normalized_statement_workbook import build_normalized_statement_workbook
from ..services.pdf_vlm_extractor import extract_pdf_statement
from ..services.statement_period_inference import (
    extract_pdf_statement_period_hint,
    infer_growth_factor_from_statement_period,
    select_statement_period_hint,
)
from ..generate_budget_pipeline import BudgetPipeline
from ..generate_budget import infer_growth_factor_from_input

logger = logging.getLogger(__name__)
router = APIRouter()


def _tmp_save_path(suffix: str = '.xlsx') -> str:
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=suffix, dir=settings.TEMP_DIR)
    os.close(fd)
    return path


def _save_file_stream(upload_file: UploadFile, path: str) -> None:
    """Write an UploadFile to disk in chunks (sync helper to run in threadpool)."""
    with open(path, "wb") as out:
        while True:
            chunk = upload_file.file.read(8192)
            if not chunk:
                break
            out.write(chunk)


async def save_upload_tmp(upload_file: UploadFile) -> str:
    """Save uploaded file to a unique temp path. Runs I/O in threadpool to avoid blocking."""
    suffix = os.path.splitext(upload_file.filename or "")[1] or ".xlsx"
    path = _tmp_save_path(suffix)
    # offload writing to threadpool because UploadFile.file is a blocking file-like
    await run_in_threadpool(_save_file_stream, upload_file, path)
    return path


@router.post("/macros/payment-search", response_model=TableResponse)
async def payment_search(file: UploadFile = File(...), sheet: str = Form('Sheet1'), background: BackgroundTasks = None):
    path = await save_upload_tmp(file)
    if background:
        background.add_task(lambda p=path: os.path.exists(p) and os.remove(p))
    try:
        # run CPU/IO heavy work in threadpool
        res = await run_in_threadpool(macros_service.payment_search_format, path, sheet)
        return res
    except Exception as e:
        # log internally and return sanitized message
        raise HTTPException(status_code=500, detail="Internal error during payment search")


@router.post("/macros/sum-and-format", response_model=SumResponse)
async def sum_and_format(
    file: UploadFile = File(...), sheet: str = Form(...), start_cell: str = Form(...), row_count: int = Form(...), target_col: str = Form('H'), background: BackgroundTasks = None
):
    path = await save_upload_tmp(file)
    if background:
        background.add_task(lambda p=path: os.path.exists(p) and os.remove(p))
    try:
        res = await run_in_threadpool(macros_service.sum_and_format, path, sheet, start_cell, row_count, target_col)
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal error during sum and format")


@router.post("/macros/ach-sum", response_model=SumResponse)
async def ach_sum(file: UploadFile = File(...), sheet: str = Form(...), start_cell: str = Form(...), find_text: str = Form('ACH Draft'), background: BackgroundTasks = None):
    path = await save_upload_tmp(file)
    if background:
        background.add_task(lambda p=path: os.path.exists(p) and os.remove(p))
    try:
        res = await run_in_threadpool(macros_service.ach_sum, path, sheet, start_cell, find_text)
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal error during ACH sum")


@router.post("/macros/one-cell", response_model=SimpleResponse)
async def one_cell(file: UploadFile = File(...), sheet: str = Form(...), start_cell: str = Form(...), background: BackgroundTasks = None):
    path = await save_upload_tmp(file)
    if background:
        background.add_task(lambda p=path: os.path.exists(p) and os.remove(p))
    try:
        r = await run_in_threadpool(macros_service.one_cell, path, sheet, start_cell)
        return {"message": f"Copied value {r.get('value')}"}
    except Exception:
        raise HTTPException(status_code=500, detail="Internal error during one-cell operation")


@router.post("/macros/find-dups", response_model=DupsResponse)
async def find_dups(file: UploadFile = File(...), sheet: str = Form(...), lookup_col: str = Form('F'), background: BackgroundTasks = None):
    path = await save_upload_tmp(file)
    if background:
        background.add_task(lambda p=path: os.path.exists(p) and os.remove(p))
    try:
        r = await run_in_threadpool(macros_service.find_dups, path, sheet, lookup_col)
        return r
    except Exception:
        raise HTTPException(status_code=500, detail="Internal error during duplicate search")


@router.post("/macros/cumulative-sum")
async def cumulative_sum(
    file: UploadFile = File(...), sheet: str = Form(...), start_row: int = Form(...), start_col: int = Form(...), end_row: int = Form(...), background: BackgroundTasks = None
):
    path = await save_upload_tmp(file)
    if background:
        background.add_task(lambda p=path: os.path.exists(p) and os.remove(p))
    try:
        r = await run_in_threadpool(macros_service.cumulative_sum, path, sheet, start_row, start_col, end_row)
        return r
    except Exception:
        raise HTTPException(status_code=500, detail="Internal error during cumulative sum")


@router.post("/macros/remove-protection")
async def remove_protection(file: UploadFile = File(...), background: BackgroundTasks = None):
    path = await save_upload_tmp(file)
    # file will be removed by background task
    if background:
        background.add_task(lambda p=path: os.path.exists(p) and os.remove(p))
    try:
        data = await run_in_threadpool(macros_service.remove_protection_return_bytes, path)
        return Response(content=data, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception:
        raise HTTPException(status_code=500, detail="Internal error removing protection")


@router.post("/macros/run-pipeline")
async def run_pipeline(file: UploadFile = File(...), sheet: str = Form('Income Statement'), background: BackgroundTasks = None):
    path = await save_upload_tmp(file)
    if background:
        background.add_task(lambda p=path: os.path.exists(p) and os.remove(p))
    try:
        r = await run_in_threadpool(macros_service.run_all_macros_pipeline, path, sheet)
        return r
    except Exception:
        raise HTTPException(status_code=500, detail="Internal error running pipeline")


@router.post("/macros/generate-budget")
async def generate_budget(
    file: UploadFile = File(...),
    growth_factor: Optional[float] = Form(None),
    fiscal_year_start_month: int = Form(1),
    reserve_contribution: Optional[float] = Form(None),
    template_file: Optional[UploadFile] = File(None),
    am_seed_file: Optional[UploadFile] = File(None),
    aliases_csv: Optional[UploadFile] = File(None),
    enrich_only: bool = Form(False),
    percent_changes_json: Optional[str] = Form(None),
    background: BackgroundTasks = None,
):
    """Run the full budget pipeline reusing existing Python code in the repo.

    V1 decision for add-budget-source-mode-switch: this developer-oriented
    route remains income-statement-only. The operator-facing HOA upload flow
    carries `source_mode`; parity here is an explicit follow-up task.

    This endpoint saves the uploaded files to temporary paths, calls the
    `generate_budget_pipeline.BudgetPipeline` runner, and returns JSON containing
    the enriched intermediate sheet and a small preview of the final budget workbook.
    """
    # Save uploaded input file
    input_path = await save_upload_tmp(file)
    if background:
        background.add_task(lambda p=input_path: os.path.exists(p) and os.remove(p))

    route = choose_financial_document_route(file.filename or "upload.xlsx", file.content_type)
    statement_period_hint: str | None = None
    if route.path == "pdf_vlm":
        extraction = await extract_pdf_statement(input_path)
        if isinstance(extraction, DocumentExtractionFailure):
            raise HTTPException(status_code=422, detail=extraction.message)
        statement_period_hint = extraction.statement_period or extract_pdf_statement_period_hint(input_path)
        input_path = await run_in_threadpool(build_normalized_statement_workbook, extraction)
        if background:
            background.add_task(lambda p=input_path: os.path.exists(p) and os.remove(p))

    # Apply percent changes from JSON before pipeline runs (writes to AM column by label)
    if percent_changes_json:
        try:
            changes = json.loads(percent_changes_json)
            await run_in_threadpool(
                macros_service.write_percent_changes_by_label, input_path, "Income Statement", changes
            )
        except Exception as e:
            logger.warning("write_percent_changes_by_label skipped: %s", e)

    tempdir = tempfile.mkdtemp(prefix="budget_pipeline_")
    try:
        intermediate_path = os.path.join(tempdir, "Income_Statement_Enriched.xlsx")
        output_path = os.path.join(tempdir, "Budget_Pipeline.xlsx")

        # Save optional ancillary uploads
        template_path = None
        am_seed_path = None
        aliases_path = None
        if template_file is not None:
            template_path = await save_upload_tmp(template_file)
            if background:
                background.add_task(lambda p=template_path: os.path.exists(p) and os.remove(p))
        if am_seed_file is not None:
            am_seed_path = await save_upload_tmp(am_seed_file)
            if background:
                background.add_task(lambda p=am_seed_path: os.path.exists(p) and os.remove(p))
        if aliases_csv is not None:
            aliases_path = await save_upload_tmp(aliases_csv)
            if background:
                background.add_task(lambda p=aliases_path: os.path.exists(p) and os.remove(p))

        # Determine growth factor if not provided
        resolved_growth_factor = growth_factor
        growth_factor_note = "configured value"
        statement_month = None
        if resolved_growth_factor is None:
            try:
                inferred = infer_growth_factor_from_statement_period(
                    statement_period_hint,
                    fiscal_year_start_month,
                )
                if inferred is None:
                    resolved_growth_factor, detected_months, source = await run_in_threadpool(
                        lambda: infer_growth_factor_from_input(input_path, fiscal_year_start_month=fiscal_year_start_month)
                    )
                else:
                    resolved_growth_factor, detected_months, source = inferred
                growth_factor_note = f"auto annualization 12/{detected_months} from {source}"
                statement_month = (fiscal_year_start_month + detected_months - 2) % 12 + 1
            except Exception as e:
                raise HTTPException(status_code=400, detail="Could not infer growth factor from input")

        # Prepare pipeline and run inside threadpool
        pipeline = BudgetPipeline(
            input_path=input_path,
            intermediate_path=intermediate_path,
            output_path=output_path,
            growth_factor=resolved_growth_factor,
            reserve_contribution=reserve_contribution,
            template_path=template_path,
            growth_factor_note=growth_factor_note,
            am_seed_workbook=am_seed_path,
            aliases_path=aliases_path,
            enrich_only=enrich_only,
        )

        try:
            await run_in_threadpool(pipeline.run)
        except Exception:
            raise HTTPException(status_code=500, detail="Budget pipeline failed")

        # Read enriched intermediate
        enriched = await run_in_threadpool(macros_service.read_sheet_as_table, intermediate_path, "Income Statement")

        # Read budget preview only when the full pipeline ran (enrich_only=False produces the output file)
        budget_preview = None
        if not enrich_only:
            budget_preview = await run_in_threadpool(
                macros_service.read_first_sheet_preview, output_path, settings.MAX_PREVIEW_ROWS
            )

        # Compose response
        resp = {
            "growth_factor": resolved_growth_factor,
            "growth_factor_note": growth_factor_note,
            "statement_month": statement_month,
            "enriched": enriched,
            "budget_preview": budget_preview,
        }
        return resp
    finally:
        # Cleanup temporary files and dirs; background tasks handle file deletes
        try:
            shutil.rmtree(tempdir, ignore_errors=True)
        except Exception as e:
            logger.warning("Temp dir cleanup failed: %s", e)
