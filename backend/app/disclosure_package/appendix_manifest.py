"""Appendix manifest resolver (Phase 5.4 — Task 159).

At compile time, the disclosure package needs a deterministic ordered
list of appendices to merge after the generated pages. The persistent
HOA appendix set lives in ``appendix_documents``; per-package overrides
(exclude, reorder, retitle) live in ``annual_package_appendices``.

Resolution rules:

* **When per-package junction rows exist for the package**: those rows
  ARE the manifest. The ``included=0`` rows are filtered out so the
  operator can omit specific appendices for one year without retiring
  them. ``override_display_title`` and ``display_order`` override the
  defaults from ``appendix_documents``. Retired appendices still appear
  if the junction row included them — prior packages should render
  reproducibly even when the source appendix has since been retired.

* **When no per-package junction rows exist**: fall back to every
  ``status='active'`` row with ``include_by_default=1`` for the
  property, ordered by ``default_display_order`` then id.

The resolver returns the data needed by the compiler — the relative
``file_id`` (so the caller can resolve the storage path), the display
title and order, plus the underlying ``appendix_id`` for audit. The
compiler is responsible for converting ``file_id`` to a filesystem path
via :func:`appendix_storage.appendix_file_path` and skipping any
missing files (matching the existing static-appendix behavior).
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from pydantic import BaseModel


class ResolvedAppendix(BaseModel):
    """One resolved appendix entry destined for the merge order."""

    appendix_id: int
    file_id: str
    file_name: str
    display_title: str
    display_order: int
    source: str  # 'override' (from junction row) | 'default' (from appendix_documents)


def resolve_appendix_manifest(
    *,
    property_id: int,
    package_id: Optional[int],
    connection: sqlite3.Connection,
) -> list[ResolvedAppendix]:
    """Return the deterministic appendix list for a package's compile.

    ``package_id=None`` is allowed for the "preview" path where there
    is no AnnualPackage yet (e.g. early-draft compile) — falls back to
    the include-by-default set unconditionally.

    The returned list is ordered by the resolved ``display_order`` then
    ``appendix_id`` so concurrent inserts with the same order still
    sort deterministically.
    """
    if package_id is not None:
        overrides = connection.execute(
            """
            SELECT ap.appendix_id,
                   ap.display_order,
                   ap.included,
                   ap.override_display_title,
                   ad.file_id,
                   ad.file_name,
                   ad.display_title
              FROM annual_package_appendices AS ap
              JOIN appendix_documents AS ad ON ad.id = ap.appendix_id
             WHERE ap.package_id = ?
               AND ad.property_id = ?
            """,
            (package_id, property_id),
        ).fetchall()
        if overrides:
            resolved = [
                ResolvedAppendix(
                    appendix_id=row[0],
                    file_id=row[4],
                    file_name=row[5],
                    display_title=row[3] or row[6],
                    display_order=row[1],
                    source="override",
                )
                for row in overrides
                if row[2]  # included = 1
            ]
            resolved.sort(key=lambda r: (r.display_order, r.appendix_id))
            return resolved

    defaults = connection.execute(
        """
        SELECT id, file_id, file_name, display_title, default_display_order
          FROM appendix_documents
         WHERE property_id = ?
           AND status = 'active'
           AND include_by_default = 1
         ORDER BY default_display_order, id
        """,
        (property_id,),
    ).fetchall()
    return [
        ResolvedAppendix(
            appendix_id=row[0],
            file_id=row[1],
            file_name=row[2],
            display_title=row[3],
            display_order=row[4],
            source="default",
        )
        for row in defaults
    ]


__all__ = ["ResolvedAppendix", "resolve_appendix_manifest"]
