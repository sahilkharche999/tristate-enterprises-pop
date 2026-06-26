"""Appendix-document service (Phase 5.4).

Persistence + upload + update + retire for ``appendix_documents`` rows.
Mirrors the DRE upload service: insert the row first to get an id,
save the file on disk using that id as prefix, then update the row's
``file_id`` to point at the saved path. Caller commits.
"""

from __future__ import annotations

import sqlite3
from typing import Literal, Optional

from pydantic import BaseModel

from app.disclosure_package import appendix_storage


class PropertyNotFound(LookupError):
    """Raised when ``property_id`` doesn't exist in the DB."""


class AppendixNotFound(LookupError):
    """Raised when ``appendix_id`` doesn't exist or isn't owned by the property."""


class AppendixDocumentResponse(BaseModel):
    """JSON shape returned by the appendix endpoints."""

    appendix_id: int
    property_id: int
    file_id: str
    file_name: str
    display_title: str
    default_display_order: int
    required_flag: bool
    include_by_default: bool
    cadence: Literal["persistent", "annual", "one_time"]
    annual_year: Optional[int]
    valid_through_year: Optional[int]
    needs_cadence_review: bool
    status: Literal["active", "retired"]
    version_int: int


def _row_to_response(row: sqlite3.Row | tuple) -> AppendixDocumentResponse:
    # Tuple positional access (works for both raw cursors and Row objects)
    return AppendixDocumentResponse(
        appendix_id=row[0],
        property_id=row[1],
        file_id=row[2],
        file_name=row[3],
        display_title=row[4],
        default_display_order=row[5],
        required_flag=bool(row[6]),
        include_by_default=bool(row[7]),
        cadence=row[8],
        annual_year=row[9],
        valid_through_year=row[10],
        needs_cadence_review=bool(row[11]),
        status=row[12],
        version_int=row[13],
    )


_SELECT_FIELDS = (
    "id, property_id, file_id, file_name, display_title, default_display_order, "
    "required_flag, include_by_default, cadence, annual_year, valid_through_year, "
    "needs_cadence_review, status, version_int"
)


def _fetch_appendix(
    connection: sqlite3.Connection, property_id: int, appendix_id: int
) -> AppendixDocumentResponse:
    row = connection.execute(
        f"SELECT {_SELECT_FIELDS} FROM appendix_documents "
        "WHERE id = ? AND property_id = ?",
        (appendix_id, property_id),
    ).fetchone()
    if row is None:
        raise AppendixNotFound(
            f"appendix_id={appendix_id} not found for property_id={property_id}"
        )
    return _row_to_response(row)


def get_appendix(
    property_id: int, appendix_id: int, connection: sqlite3.Connection
) -> AppendixDocumentResponse:
    """Return a single appendix row, raising AppendixNotFound if missing."""
    return _fetch_appendix(connection, property_id, appendix_id)


def upload_appendix(
    *,
    property_id: int,
    file_bytes: bytes,
    original_filename: str,
    display_title: str,
    cadence: Literal["persistent", "annual", "one_time"] = "persistent",
    annual_year: Optional[int] = None,
    valid_through_year: Optional[int] = None,
    required_flag: bool = False,
    include_by_default: bool = True,
    uploaded_by: Optional[str],
    connection: sqlite3.Connection,
) -> AppendixDocumentResponse:
    """Create a new ``appendix_documents`` row + save the PDF to disk.

    Caller (the router) supplies the bytes and metadata. Caller is
    responsible for the SQLAlchemy session boundary; this function
    commits the raw connection itself so the row + file are atomically
    visible from the caller's perspective.

    Raises:
        PropertyNotFound: when ``property_id`` doesn't match a row.
    """
    if cadence not in ("persistent", "annual", "one_time"):
        raise ValueError(
            f"Unknown cadence {cadence!r}; expected persistent | annual | one_time"
        )

    property_row = connection.execute(
        "SELECT id FROM properties WHERE id = ?", (property_id,)
    ).fetchone()
    if property_row is None:
        raise PropertyNotFound(f"property_id={property_id} does not exist")

    cur = connection.execute(
        """
        INSERT INTO appendix_documents (
            property_id, file_id, file_name, display_title,
            default_display_order, required_flag, include_by_default,
            cadence, annual_year, valid_through_year,
            needs_cadence_review, status, uploaded_by
        ) VALUES (?, 'pending', ?, ?, 0, ?, ?, ?, ?, ?, 0, 'active', ?)
        """,
        (
            property_id, original_filename, display_title,
            int(required_flag), int(include_by_default),
            cadence, annual_year, valid_through_year, uploaded_by,
        ),
    )
    appendix_id = cur.lastrowid
    if appendix_id is None:
        raise RuntimeError("sqlite did not return a lastrowid for appendix_documents")

    file_id = appendix_storage.save_appendix_file(
        property_id=property_id,
        file_bytes=file_bytes,
        original_filename=original_filename,
        appendix_id=appendix_id,
    )

    connection.execute(
        "UPDATE appendix_documents SET file_id = ? WHERE id = ?",
        (file_id, appendix_id),
    )
    connection.commit()
    return _fetch_appendix(connection, property_id, appendix_id)


def list_appendices(
    *,
    property_id: int,
    connection: sqlite3.Connection,
    include_retired: bool = False,
) -> list[AppendixDocumentResponse]:
    """Return every appendix for an HOA, ordered by display order.

    Retired rows are excluded by default so a fresh AnnualPackage
    only inherits live appendices; pass ``include_retired=True`` for
    the audit view in the Settings tab.
    """
    where = "property_id = ?"
    params: tuple = (property_id,)
    if not include_retired:
        where += " AND status = 'active'"
    rows = connection.execute(
        f"SELECT {_SELECT_FIELDS} FROM appendix_documents "
        f"WHERE {where} ORDER BY default_display_order, id",
        params,
    ).fetchall()
    return [_row_to_response(r) for r in rows]


def update_appendix(
    *,
    property_id: int,
    appendix_id: int,
    expected_version: int,
    display_title: Optional[str] = None,
    default_display_order: Optional[int] = None,
    include_by_default: Optional[bool] = None,
    cadence: Optional[Literal["persistent", "annual", "one_time"]] = None,
    annual_year: Optional[int] = None,
    valid_through_year: Optional[int] = None,
    required_flag: Optional[bool] = None,
    needs_cadence_review: Optional[bool] = None,
    connection: sqlite3.Connection,
) -> AppendixDocumentResponse:
    """Partial-update an appendix row with optimistic concurrency control.

    ``expected_version`` is the ``version_int`` the client read; the
    UPDATE writes ``version_int = expected + 1`` and fails when the row's
    current value doesn't match (concurrent edit). Returns the refreshed
    row.

    Raises:
        AppendixNotFound: when row missing OR version mismatch.
    """
    current = _fetch_appendix(connection, property_id, appendix_id)
    if current.version_int != expected_version:
        raise AppendixNotFound(
            f"appendix_id={appendix_id} version mismatch "
            f"(expected={expected_version}, got={current.version_int})"
        )

    updates: list[str] = []
    values: list = []
    if display_title is not None:
        updates.append("display_title = ?")
        values.append(display_title)
    if default_display_order is not None:
        updates.append("default_display_order = ?")
        values.append(default_display_order)
    if include_by_default is not None:
        updates.append("include_by_default = ?")
        values.append(int(include_by_default))
    if cadence is not None:
        if cadence not in ("persistent", "annual", "one_time"):
            raise ValueError(f"Unknown cadence {cadence!r}")
        updates.append("cadence = ?")
        values.append(cadence)
    if annual_year is not None:
        updates.append("annual_year = ?")
        values.append(annual_year)
    if valid_through_year is not None:
        updates.append("valid_through_year = ?")
        values.append(valid_through_year)
    if required_flag is not None:
        updates.append("required_flag = ?")
        values.append(int(required_flag))
    if needs_cadence_review is not None:
        updates.append("needs_cadence_review = ?")
        values.append(int(needs_cadence_review))

    if not updates:
        return current  # no-op update; return current state

    updates.append("version_int = version_int + 1")
    values.extend([appendix_id, property_id, expected_version])

    connection.execute(
        f"UPDATE appendix_documents SET {', '.join(updates)} "
        "WHERE id = ? AND property_id = ? AND version_int = ?",
        tuple(values),
    )
    connection.commit()
    return _fetch_appendix(connection, property_id, appendix_id)


def retire_appendix(
    *,
    property_id: int,
    appendix_id: int,
    connection: sqlite3.Connection,
) -> AppendixDocumentResponse:
    """Mark an appendix ``status='retired'`` without deleting the row.

    Prior packages may still reference the appendix via
    ``annual_package_appendices``; a hard delete would orphan those.
    """
    _fetch_appendix(connection, property_id, appendix_id)  # validates existence
    connection.execute(
        "UPDATE appendix_documents SET status = 'retired' "
        "WHERE id = ? AND property_id = ?",
        (appendix_id, property_id),
    )
    connection.commit()
    return _fetch_appendix(connection, property_id, appendix_id)
