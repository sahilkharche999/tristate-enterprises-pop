"""Operator-triggered CC&R / governing-document extraction service.

Mirrors dre_extraction_service.py — uses the CC&R pipeline from
governing_doc_extraction/ but reuses the same DRE persistence helpers
(create_placeholder_extraction_run, finalize_extraction_run, etc.) since
both document types share the dre_extraction_runs table (discriminated
by document_type='ccr').
"""

from __future__ import annotations

import logging
import sqlite3
from typing import NamedTuple, Optional

from app.config import settings
from app.dre_extraction.page_rendering import render_dre_pages
from app.dre_extraction.persistence import (
    create_placeholder_extraction_run,
    finalize_extraction_run,
    find_active_extraction_run,
    mark_extraction_run_failed,
    mark_extraction_run_running,
)
from app.dre_extraction.storage import dre_file_exists, dre_file_path
from app.governing_doc_extraction.gemini_callbacks import (
    build_classify_callback,
    build_extract_callback,
    build_repair_callback,
    default_model_name,
    gemini_client_from_env,
)
from app.governing_doc_extraction.pipeline import run_ccr_extraction
from app.governing_doc_extraction.prompts import (
    CCR_POLICY_EXTRACTOR_PROMPT_SHA256,
    CCR_POLICY_EXTRACTOR_PROMPT_VERSION,
)
from app.governing_doc_extraction.wire_schemas import CCR_WIRE_SCHEMA_SHA256

logger = logging.getLogger(__name__)


class CCRExtractionPreconditionError(Exception):
    """Document missing, file missing, or wrong HOA."""


class ScheduledCCRExtractionResult(NamedTuple):
    extraction_run_id: int
    job_status: str
    status: str


def lookup_ccr_document(
    *,
    property_id: int,
    document_id: int,
    connection: sqlite3.Connection,
) -> tuple[str, str]:
    """Return (file_id, file_name) for an extractable CC&R document."""
    row = connection.execute(
        "SELECT file_id, file_name, property_id "
        "FROM dre_documents WHERE id = ? AND document_type = 'ccr'",
        (document_id,),
    ).fetchone()
    if row is None:
        raise CCRExtractionPreconditionError(
            f"CC&R document {document_id} not found"
        )
    file_id, file_name, doc_property_id = row[0], row[1], row[2]
    if int(doc_property_id) != int(property_id):
        raise CCRExtractionPreconditionError(
            f"CC&R document {document_id} belongs to a different HOA"
        )
    if not dre_file_exists(file_id):
        raise CCRExtractionPreconditionError(
            f"CC&R document {document_id} file missing from storage"
        )
    return file_id, file_name


def schedule_ccr_extraction_run(
    *,
    property_id: int,
    document_id: int,
    connection: sqlite3.Connection,
) -> ScheduledCCRExtractionResult:
    """Create placeholder run unless one active run exists for the document."""
    active = find_active_extraction_run(
        dre_document_id=document_id,
        connection=connection,
    )
    if active is not None:
        return ScheduledCCRExtractionResult(
            extraction_run_id=active[0],
            job_status=active[1],
            status="already_running",
        )

    model_name = default_model_name()
    try:
        run_id = create_placeholder_extraction_run(
            property_id=property_id,
            dre_document_id=document_id,
            model_name=model_name,
            prompt_version=CCR_POLICY_EXTRACTOR_PROMPT_VERSION,
            prompt_sha256=CCR_POLICY_EXTRACTOR_PROMPT_SHA256,
            wire_schema_sha256=CCR_WIRE_SCHEMA_SHA256,
            connection=connection,
        )
        # Tag the run with document_type='ccr' so listings filter correctly.
        connection.execute(
            "UPDATE dre_extraction_runs SET document_type = 'ccr' WHERE id = ?",
            (run_id,),
        )
        connection.commit()
        return ScheduledCCRExtractionResult(
            extraction_run_id=run_id,
            job_status="queued",
            status="scheduled",
        )
    except sqlite3.IntegrityError:
        connection.rollback()
        active = find_active_extraction_run(
            dre_document_id=document_id,
            connection=connection,
        )
        if active is None:
            raise
        return ScheduledCCRExtractionResult(
            extraction_run_id=active[0],
            job_status=active[1],
            status="already_running",
        )


def run_ccr_extraction_job(
    *,
    run_id: int,
    property_id: int,
    document_id: int,
    file_id: str,
    max_pages: Optional[int] = None,
    db_path: Optional[str] = None,
) -> None:
    """BackgroundTask entry point for CC&R extraction."""
    connection = _resolve_connection(db_path)
    try:
        mark_extraction_run_running(extraction_run_id=run_id, connection=connection)
        connection.commit()

        client = gemini_client_from_env()
        if client is None:
            raise RuntimeError(
                "Gemini client unavailable — check GEMINI_API_KEY and GEMINI_MODEL"
            )
        model = settings.GEMINI_MODEL
        if not model:
            raise RuntimeError("GEMINI_MODEL is not configured")

        pdf_path = dre_file_path(file_id)
        logger.info(
            "CC&R extraction starting: property=%s document=%s model=%s pdf=%s",
            property_id, document_id, model, pdf_path.name,
        )

        rendered = render_dre_pages(str(pdf_path), max_pages=max_pages)
        rendered_by_num = {r.page_number: r for r in rendered}
        page_count = len(rendered)

        record = run_ccr_extraction(
            page_count=page_count,
            classify_pages_callback=build_classify_callback(
                client, model=model, rendered_pages_by_num=rendered_by_num,
            ),
            extract_policy_callback=build_extract_callback(
                client, model=model, rendered_pages_by_num=rendered_by_num,
            ),
            repair_callback=build_repair_callback(client, model=model),
            model_name=model,
        )

        finalize_extraction_run(
            record,  # type: ignore[arg-type]
            extraction_run_id=run_id,
            connection=connection,
            wire_schema_sha256=CCR_WIRE_SCHEMA_SHA256,
        )
        connection.commit()

        logger.info(
            "CC&R extraction complete: property=%s document=%s run_id=%s status=%s",
            property_id, document_id, run_id, record.status,
        )
    except Exception as exc:  # noqa: BLE001 — BackgroundTask must never raise
        try:
            mark_extraction_run_failed(
                extraction_run_id=run_id,
                error_message=str(exc),
                connection=connection,
            )
            connection.commit()
        except Exception:
            logger.exception(
                "Failed to persist CC&R extraction failure: run_id=%s", run_id
            )
        logger.exception(
            "CC&R extraction crashed: property=%s document=%s run_id=%s",
            property_id, document_id, run_id,
        )
    finally:
        connection.close()


def _resolve_connection(db_path: Optional[str]) -> sqlite3.Connection:
    if db_path is not None:
        return sqlite3.connect(db_path)
    from app.ai_implementation.db.session import engine
    return engine.raw_connection()


__all__ = [
    "CCRExtractionPreconditionError",
    "ScheduledCCRExtractionResult",
    "lookup_ccr_document",
    "schedule_ccr_extraction_run",
    "run_ccr_extraction_job",
]
