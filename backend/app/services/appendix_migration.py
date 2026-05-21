"""Old Mill → AppendixDocument migration (Phase 5.4 tasks 160 + 166).

Walks the legacy ``backend/app/disclosure_package/appendices/old_mill/``
directory (or any compatible directory) and inserts one
``appendix_documents`` row per PDF, seeded with:

* ``display_title`` inferred from the filename (stripped of underscores
  and ``.pdf`` extension)
* ``cadence='persistent'`` as the safe default
* ``needs_cadence_review=True`` so the operator confirms cadence
  before the next package year (task 166)
* ``include_by_default=True`` so renders pick them up automatically
* A suggested ``cadence='annual'`` if the filename or display title
  contains ``insurance`` (case-insensitive) — heuristic per task 166

Idempotent: skips files already represented in the DB by name +
property pair. Returns the count of rows inserted.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_ANNUAL_HEURISTIC_RE = re.compile(r"insurance", re.IGNORECASE)
_TITLE_CLEAN_RE = re.compile(r"[_\-]+")


def _infer_display_title(filename: str) -> str:
    stem = Path(filename).stem
    cleaned = _TITLE_CLEAN_RE.sub(" ", stem).strip()
    return " ".join(w.capitalize() for w in cleaned.split()) or filename


def _infer_cadence(filename: str, display_title: str) -> str:
    """Suggest 'annual' for insurance-flavored appendix files."""
    for s in (filename, display_title):
        if _ANNUAL_HEURISTIC_RE.search(s):
            return "annual"
    return "persistent"


def migrate_directory_to_appendix_documents(
    *,
    property_id: int,
    appendix_dir: Path,
    fiscal_year_hint: Optional[int] = None,
    connection: sqlite3.Connection,
) -> int:
    """Insert ``appendix_documents`` rows for every PDF in
    ``appendix_dir``. Returns the count of rows inserted.

    Existing rows (matched by ``property_id`` + ``file_name``) are
    left alone so re-running the migration is idempotent.
    """
    if not appendix_dir.exists() or not appendix_dir.is_dir():
        logger.info(
            "appendix_migration: directory %s does not exist; nothing to migrate",
            appendix_dir,
        )
        return 0

    existing_filenames = {
        row[0]
        for row in connection.execute(
            "SELECT file_name FROM appendix_documents WHERE property_id = ?",
            (property_id,),
        ).fetchall()
    }

    inserted = 0
    for idx, pdf_path in enumerate(sorted(appendix_dir.glob("*.pdf"))):
        if pdf_path.name in existing_filenames:
            continue

        display_title = _infer_display_title(pdf_path.name)
        cadence = _infer_cadence(pdf_path.name, display_title)

        annual_year = fiscal_year_hint if cadence == "annual" else None
        valid_through_year = (
            fiscal_year_hint if cadence == "annual" else None
        )

        connection.execute(
            """
            INSERT INTO appendix_documents (
                property_id, file_id, file_name, display_title,
                default_display_order, required_flag, include_by_default,
                cadence, annual_year, valid_through_year,
                needs_cadence_review, status, uploaded_by
            ) VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?, ?, 1, 'active', 'migration')
            """,
            (
                property_id, f"legacy/{pdf_path.name}", pdf_path.name,
                display_title, idx,
                cadence, annual_year, valid_through_year,
            ),
        )
        inserted += 1

    if inserted:
        connection.commit()
    return inserted


__all__ = [
    "migrate_directory_to_appendix_documents",
]
