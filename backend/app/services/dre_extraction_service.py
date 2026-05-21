"""Operator-triggered DRE extraction (POST /hoa/{id}/dre/documents/{doc_id}/extract).

The router schedules ``run_extraction_job`` as a FastAPI BackgroundTask
when the operator clicks "Run extraction" on an uploaded DRE document.
The job:

1. Loads the stored PDF bytes from BUDGET_STORAGE_ROOT/dre/...
2. Renders each page to a PNG via ``render_dre_pages``
3. Builds real Gemini callbacks via ``gemini_callbacks`` (same code path
   the live tests exercise)
4. Calls ``run_dre_extraction`` to produce a ``DREExtractionRunRecord``
5. Persists the run via ``save_extraction_run``
6. Logs success/failure; never raises out of a BackgroundTask

The router returns 202 immediately; the UI polls
``GET /hoa/{id}/dre/extraction-runs`` to discover the new row.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from ..dre_extraction.gemini_callbacks import (
    build_classify_callback,
    build_extract_callback,
    build_repair_callback,
    default_model_name,
    gemini_client_from_env,
)
from ..dre_extraction.page_rendering import render_dre_pages
from ..dre_extraction.persistence import save_extraction_run
from ..dre_extraction.wire_schemas import WIRE_SCHEMA_SHA256
from ..dre_extraction.pipeline import run_dre_extraction
from ..dre_extraction.storage import dre_file_exists, dre_file_path

logger = logging.getLogger(__name__)


class DREExtractionPreconditionError(Exception):
    """Raised by the router-side validation: document missing, file missing, etc."""


def lookup_dre_document(
    *,
    property_id: int,
    dre_document_id: int,
    connection: sqlite3.Connection,
) -> tuple[str, str]:
    """Return ``(file_id, file_name)`` for an extractable DRE document.

    Raises ``DREExtractionPreconditionError`` if the row doesn't exist,
    doesn't belong to ``property_id``, or its file_id isn't on disk.
    """
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


def run_extraction_job(
    *,
    property_id: int,
    dre_document_id: int,
    file_id: str,
    max_pages: Optional[int] = None,
    db_path: Optional[str] = None,
) -> None:
    """BackgroundTask entry point. Renders + extracts + persists.

    Never raises — exceptions are logged. The router has already
    validated preconditions before scheduling this.

    ``db_path`` lets tests inject an explicit SQLite file; production
    pulls the SQLAlchemy engine's raw connection (via the local
    import so this module stays import-cycle-safe).
    """
    try:
        client = gemini_client_from_env()
        if client is None:
            logger.error(
                "DRE extraction skipped: GEMINI_API_KEY not set "
                "(property=%s document=%s)",
                property_id, dre_document_id,
            )
            return

        model = default_model_name()
        pdf_path = dre_file_path(file_id)
        if not pdf_path.exists():
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

        connection = _resolve_connection(db_path)
        try:
            run_id = save_extraction_run(
                record,
                property_id=property_id,
                dre_document_id=dre_document_id,
                connection=connection,
                wire_schema_sha256=WIRE_SCHEMA_SHA256,
            )
            connection.commit()
        finally:
            connection.close()

        logger.info(
            "DRE extraction complete: property=%s document=%s run_id=%s status=%s",
            property_id, dre_document_id, run_id, record.status,
        )
    except Exception:  # noqa: BLE001 — BackgroundTask must never raise
        logger.exception(
            "DRE extraction crashed: property=%s document=%s",
            property_id, dre_document_id,
        )


def _resolve_connection(db_path: Optional[str]) -> sqlite3.Connection:
    if db_path is not None:
        return sqlite3.connect(db_path)
    from ..ai_implementation.db.session import engine
    return engine.raw_connection()


__all__ = [
    "DREExtractionPreconditionError",
    "lookup_dre_document",
    "run_extraction_job",
]
