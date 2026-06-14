"""Operator-triggered DRE extraction (POST /hoa/{id}/dre/documents/{doc_id}/extract).

The router schedules ``run_extraction_job`` as a FastAPI BackgroundTask
when the operator clicks "Run extraction" on an uploaded DRE document.
Unlike the original version, this service now persists a placeholder
``dre_extraction_runs`` row immediately so the UI can rediscover the run
after navigation or refresh.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import NamedTuple, Optional

from ..dre_extraction.gemini_callbacks import (
    build_classify_callback,
    build_extract_callback,
    build_repair_callback,
    default_model_name,
    gemini_client_from_env,
)
from ..config import settings
from ..dre_extraction.page_rendering import render_dre_pages
from ..dre_extraction.persistence import (
    create_placeholder_extraction_run,
    finalize_extraction_run,
    find_active_extraction_run,
    get_requested_model_name,
    mark_extraction_run_failed,
    mark_extraction_run_running,
)
from ..dre_extraction.pipeline import run_dre_extraction
from ..dre_extraction.prompts import (
    DRE_SETUP_EXTRACTOR_PROMPT_SHA256,
    DRE_SETUP_EXTRACTOR_PROMPT_VERSION,
)
from ..dre_extraction.storage import dre_file_exists, dre_file_path
from ..dre_extraction.wire_schemas import WIRE_SCHEMA_SHA256

logger = logging.getLogger(__name__)


class DREExtractionPreconditionError(Exception):
    """Raised by router-side validation: document missing, file missing, etc."""


class ScheduledDREExtractionResult(NamedTuple):
    extraction_run_id: int
    job_status: str
    status: str


def lookup_dre_document(
    *,
    property_id: int,
    dre_document_id: int,
    connection: sqlite3.Connection,
) -> tuple[str, str]:
    """Return ``(file_id, file_name)`` for an extractable DRE document."""
    row = connection.execute(
        "SELECT file_id, file_name, property_id "
        "FROM dre_documents WHERE id = ?",
        (dre_document_id,),
    ).fetchone()
    if row is None:
        raise DREExtractionPreconditionError(
            f"DRE document {dre_document_id} not found"
        )
    file_id, file_name, doc_property_id = row[0], row[1], row[2]
    if int(doc_property_id) != int(property_id):
        raise DREExtractionPreconditionError(
            f"DRE document {dre_document_id} belongs to a different HOA"
        )
    if not dre_file_exists(file_id):
        raise DREExtractionPreconditionError(
            f"DRE document {dre_document_id} file missing from storage"
        )
    return file_id, file_name


def schedule_extraction_run(
    *,
    property_id: int,
    dre_document_id: int,
    connection: sqlite3.Connection,
) -> ScheduledDREExtractionResult:
    """Create placeholder row unless one active run already exists."""
    active = find_active_extraction_run(
        dre_document_id=dre_document_id,
        connection=connection,
    )
    if active is not None:
        return ScheduledDREExtractionResult(
            extraction_run_id=active[0],
            job_status=active[1],
            status="already_running",
        )

    model_name = default_model_name()
    try:
        run_id = create_placeholder_extraction_run(
            property_id=property_id,
            dre_document_id=dre_document_id,
            model_name=model_name,
            prompt_version=DRE_SETUP_EXTRACTOR_PROMPT_VERSION,
            prompt_sha256=DRE_SETUP_EXTRACTOR_PROMPT_SHA256,
            wire_schema_sha256=WIRE_SCHEMA_SHA256,
            connection=connection,
        )
        connection.commit()
        return ScheduledDREExtractionResult(
            extraction_run_id=run_id,
            job_status="queued",
            status="scheduled",
        )
    except sqlite3.IntegrityError:
        connection.rollback()
        active = find_active_extraction_run(
            dre_document_id=dre_document_id,
            connection=connection,
        )
        if active is None:
            raise
        return ScheduledDREExtractionResult(
            extraction_run_id=active[0],
            job_status=active[1],
            status="already_running",
        )


def run_extraction_job(
    *,
    run_id: int,
    property_id: int,
    dre_document_id: int,
    file_id: str,
    max_pages: Optional[int] = None,
    db_path: Optional[str] = None,
) -> None:
    """BackgroundTask entry point. Renders + extracts + updates existing run."""
    connection = _resolve_connection(db_path)
    try:
        mark_extraction_run_running(
            extraction_run_id=run_id,
            connection=connection,
        )
        connection.commit()

        client = gemini_client_from_env()
        if client is None:
            auth_hint = (
                "Vertex/ADC configuration incomplete"
                if settings.GOOGLE_GENAI_USE_ENTERPRISE
                else "GEMINI_API_KEY not set"
            )
            _persist_failed_job(
                connection=connection,
                run_id=run_id,
                message=f"DRE extraction skipped: {auth_hint}",
            )
            logger.error(
                "DRE extraction skipped: %s "
                "(property=%s document=%s)",
                auth_hint, property_id, dre_document_id,
            )
            return

        model = (
            get_requested_model_name(
                extraction_run_id=run_id,
                connection=connection,
            )
            or default_model_name()
        )
        pdf_path = dre_file_path(file_id)
        if not pdf_path.exists():
            _persist_failed_job(
                connection=connection,
                run_id=run_id,
                message=f"DRE extraction aborted: file missing {pdf_path}",
            )
            logger.error(
                "DRE extraction aborted: file missing %s "
                "(property=%s document=%s)",
                pdf_path, property_id, dre_document_id,
            )
            return

        logger.info(
            "DRE extraction starting: property=%s document=%s model=%s pdf=%s",
            property_id, dre_document_id, model, pdf_path.name,
        )

        rendered = render_dre_pages(str(pdf_path), max_pages=max_pages)
        rendered_by_num = {r.page_number: r for r in rendered}
        page_count = len(rendered)

        record = run_dre_extraction(
            page_count=page_count,
            classify_pages_callback=build_classify_callback(
                client, model=model, rendered_pages_by_num=rendered_by_num,
            ),
            extract_setup_callback=build_extract_callback(
                client, model=model, rendered_pages_by_num=rendered_by_num,
            ),
            repair_callback=build_repair_callback(client, model=model),
            model_name=model,
        )

        finalize_extraction_run(
            record,
            extraction_run_id=run_id,
            connection=connection,
            wire_schema_sha256=WIRE_SCHEMA_SHA256,
        )
        connection.commit()

        logger.info(
            "DRE extraction complete: property=%s document=%s run_id=%s status=%s",
            property_id, dre_document_id, run_id, record.status,
        )
    except Exception as exc:  # noqa: BLE001 — BackgroundTask must never raise
        try:
            _persist_failed_job(
                connection=connection,
                run_id=run_id,
                message=str(exc),
            )
        except Exception:
            logger.exception(
                "Failed to persist DRE extraction failure: property=%s document=%s run_id=%s",
                property_id, dre_document_id, run_id,
            )
        logger.exception(
            "DRE extraction crashed: property=%s document=%s run_id=%s",
            property_id, dre_document_id, run_id,
        )
    finally:
        connection.close()


def _persist_failed_job(
    *,
    connection: sqlite3.Connection,
    run_id: int,
    message: str,
) -> None:
    mark_extraction_run_failed(
        extraction_run_id=run_id,
        error_message=message,
        connection=connection,
    )
    connection.commit()


def _resolve_connection(db_path: Optional[str]) -> sqlite3.Connection:
    if db_path is not None:
        return sqlite3.connect(db_path)
    from ..ai_implementation.db.session import engine
    return engine.raw_connection()


__all__ = [
    "DREExtractionPreconditionError",
    "ScheduledDREExtractionResult",
    "lookup_dre_document",
    "run_extraction_job",
    "schedule_extraction_run",
]
