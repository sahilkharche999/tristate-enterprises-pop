"""SQLite persistence for DRE extraction runs.

Maps the in-memory ``DREExtractionRunRecord`` (built by
``pipeline.run_dre_extraction``) into a ``dre_extraction_runs`` DB row,
serializing the audit-trail fields as JSON. Phase 3.1 task: "Store every
run in DREExtractionRun with: model_name, prompt_version, prompt_sha256,
raw_model_output, parsed_json, schema_validation_errors,
repair_attempt_count".

The function is connection-injected for testability — pass any
DBAPI-compatible connection (sqlite3.Connection or the raw_connection
from the SQLAlchemy engine). No router wiring here; that's Phase 3.1
task 3 (POST /hoa/{id}/dre/upload).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Any, Optional

from .pipeline import DREExtractionRunRecord
from .validation import CitationAudit, LowConfidenceFlag

ACTIVE_DRE_JOB_STATUSES = ("queued", "running")


def _serialize_citation_audit(audit: Optional[CitationAudit]) -> str:
    if audit is None:
        return "[]"
    return json.dumps(audit.missing_citations)


def _serialize_low_confidence_flags(flags: list[LowConfidenceFlag]) -> str:
    return json.dumps([{"path": f.path, "confidence": f.confidence} for f in flags])


def find_active_extraction_run(
    *,
    dre_document_id: int,
    connection: sqlite3.Connection,
) -> Optional[tuple[int, str]]:
    row = connection.execute(
        """
        SELECT id, job_status
          FROM dre_extraction_runs
         WHERE dre_document_id = ?
           AND job_status IN ('queued', 'running')
         ORDER BY started_at DESC, id DESC
         LIMIT 1
        """,
        (dre_document_id,),
    ).fetchone()
    if row is None:
        return None
    return int(row[0]), str(row[1])


def create_placeholder_extraction_run(
    *,
    property_id: int,
    dre_document_id: int,
    model_name: str,
    prompt_version: str,
    prompt_sha256: str,
    wire_schema_sha256: str,
    connection: sqlite3.Connection,
) -> int:
    cur = connection.execute(
        """
        INSERT INTO dre_extraction_runs (
            dre_document_id, property_id,
            model_name, prompt_version, prompt_sha256,
            job_status, wire_schema_sha256
        ) VALUES (?, ?, ?, ?, ?, 'queued', ?)
        """,
        (
            dre_document_id,
            property_id,
            model_name,
            prompt_version,
            prompt_sha256,
            wire_schema_sha256,
        ),
    )
    run_id = cur.lastrowid
    if run_id is None:
        raise RuntimeError("sqlite did not return a lastrowid for the placeholder extraction run")
    return run_id


def get_requested_model_name(
    *,
    extraction_run_id: int,
    connection: sqlite3.Connection,
) -> Optional[str]:
    row = connection.execute(
        "SELECT model_name FROM dre_extraction_runs WHERE id = ?",
        (extraction_run_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row[0] or "")


def mark_extraction_run_running(
    *,
    extraction_run_id: int,
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        UPDATE dre_extraction_runs
           SET job_status = 'running',
               error_message = ''
         WHERE id = ?
        """,
        (extraction_run_id,),
    )


def finalize_extraction_run(
    record: DREExtractionRunRecord,
    *,
    extraction_run_id: int,
    connection: sqlite3.Connection,
    wire_schema_sha256: str = "",
) -> None:
    connection.execute(
        """
        UPDATE dre_extraction_runs
           SET completed_at = datetime('now'),
               model_name = ?,
               prompt_version = ?,
               prompt_sha256 = ?,
               raw_model_output = ?,
               parsed_json = ?,
               schema_validation_errors = ?,
               repair_attempt_count = ?,
               citation_audit_json = ?,
               low_confidence_flags_json = ?,
               validation_warnings_json = ?,
               job_status = 'completed',
               status = ?,
               error_message = '',
               wire_schema_sha256 = ?,
               model_version_resolved = ?,
               finish_reason = ?,
               output_tokens_used = ?
         WHERE id = ?
        """,
        (
            record.model_name,
            record.prompt_version,
            record.prompt_sha256,
            record.raw_model_output,
            json.dumps(record.parsed_json) if record.parsed_json is not None else None,
            json.dumps(record.schema_validation_errors),
            record.repair_attempt_count,
            _serialize_citation_audit(record.citation_audit),
            _serialize_low_confidence_flags(record.low_confidence_flags),
            json.dumps(record.validation_warnings),
            record.status,
            wire_schema_sha256,
            record.model_version_resolved,
            record.finish_reason,
            record.output_tokens_used,
            extraction_run_id,
        ),
    )


def mark_extraction_run_failed(
    *,
    extraction_run_id: int,
    error_message: str,
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        UPDATE dre_extraction_runs
           SET completed_at = datetime('now'),
               job_status = 'failed',
               status = 'failed',
               error_message = ?
         WHERE id = ?
        """,
        (error_message, extraction_run_id),
    )


def save_extraction_run(
    record: DREExtractionRunRecord,
    *,
    property_id: int,
    dre_document_id: int,
    connection: sqlite3.Connection,
    wire_schema_sha256: str = "",
) -> int:
    """Persist a single run record to ``dre_extraction_runs``.

    ``wire_schema_sha256`` is the SHA-256 of the wire-schema's JSON
    Schema representation at the time of this run, captured so audit
    can detect schema-source-of-truth drift independently of prompt
    drift. Pre-structured-output callers leave it as ``""``.

    Returns the new row's id. Caller commits — this function does not
    open or commit its own transaction.
    """
    cur = connection.execute(
        """
        INSERT INTO dre_extraction_runs (
            dre_document_id, property_id, completed_at,
            model_name, prompt_version, prompt_sha256,
            raw_model_output, parsed_json,
            schema_validation_errors, repair_attempt_count,
            citation_audit_json, low_confidence_flags_json,
            validation_warnings_json, job_status, status, review_status,
            wire_schema_sha256,
            model_version_resolved, finish_reason, output_tokens_used
        ) VALUES (
            ?, ?, datetime('now'),
            ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, 'completed', ?, 'pending',
            ?,
            ?, ?, ?
        )
        """,
        (
            dre_document_id,
            property_id,
            record.model_name,
            record.prompt_version,
            record.prompt_sha256,
            record.raw_model_output,
            json.dumps(record.parsed_json) if record.parsed_json is not None else None,
            json.dumps(record.schema_validation_errors),
            record.repair_attempt_count,
            _serialize_citation_audit(record.citation_audit),
            _serialize_low_confidence_flags(record.low_confidence_flags),
            json.dumps(record.validation_warnings),
            record.status,
            wire_schema_sha256,
            record.model_version_resolved,
            record.finish_reason,
            record.output_tokens_used,
        ),
    )
    run_id = cur.lastrowid
    if run_id is None:
        raise RuntimeError("sqlite did not return a lastrowid for the extraction run")
    return run_id


def insert_dre_document(
    *,
    property_id: int,
    file_id: str,
    file_name: str,
    page_count: Optional[int],
    uploaded_by: Optional[str],
    connection: sqlite3.Connection,
) -> int:
    """Insert a ``dre_documents`` row and mark prior 'active' rows for
    the same property as ``superseded``.

    Returns the new document id. Caller commits.
    """
    prior = connection.execute(
        "SELECT id FROM dre_documents "
        "WHERE property_id = ? AND status = 'active'",
        (property_id,),
    ).fetchall()

    cur = connection.execute(
        """
        INSERT INTO dre_documents (
            property_id, file_id, file_name, page_count,
            supersedes_id, status, uploaded_by
        ) VALUES (?, ?, ?, ?, ?, 'active', ?)
        """,
        (
            property_id,
            file_id,
            file_name,
            page_count,
            prior[0][0] if prior else None,
            uploaded_by,
        ),
    )
    new_id = cur.lastrowid
    if new_id is None:
        raise RuntimeError("sqlite did not return a lastrowid for the dre_document")

    if prior:
        connection.executemany(
            "UPDATE dre_documents SET status = 'superseded' WHERE id = ?",
            [(row[0],) for row in prior],
        )
    return new_id
