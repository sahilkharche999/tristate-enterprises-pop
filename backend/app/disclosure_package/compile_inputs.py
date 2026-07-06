"""Compile-side input resolution (Phase 4.8 tasks 135 + 159).

Bridges the AnnualPackage state to the typed inputs ``compile_package``
already consumes. Two responsibilities:

* :func:`should_use_snapshots` — picks the snapshot-vs-live branch
  based on ``annual_packages.status``. Finalized packages MUST load
  from frozen snapshots so re-renders byte-match the original.

* :func:`resolve_compile_appendix_paths` — builds the deterministic
  ordered list of appendix PDF paths for the merge step, applying the
  per-package overrides + the include-by-default fallback from
  :func:`appendix_manifest.resolve_appendix_manifest`.

* :func:`compile_input_summary` — a debug-friendly dict of "which
  branch did we take + which appendices resolved". Useful for the
  audit log so the operator can see why a re-render produced what it
  did.

Note: the broader snapshot-vs-live deserialization of BudgetDraft /
ReserveStudySnapshot / AssessmentSetup from frozen JSON columns lives
on top of these primitives — see ``snapshots.load_package_snapshots``
for the raw payloads. Conversion back into typed engine inputs is the
caller's job; the helpers in this module are the deterministic-path
boundary only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from .appendix_manifest import ResolvedAppendix, resolve_appendix_manifest
from .appendix_storage import appendix_file_path, appendix_file_exists


def should_use_snapshots(
    *,
    package_id: Optional[int],
    connection: sqlite3.Connection,
) -> bool:
    """Return True when the compile MUST load from frozen snapshots.

    True when ``annual_packages.status='finalized'`` and all four
    snapshot columns are non-null. False otherwise (draft/approved/
    rendered all read live state).

    ``package_id=None`` is permitted for ad-hoc previews — returns
    False so the compile reads live state.
    """
    if package_id is None:
        return False
    row = connection.execute(
        """
        SELECT status,
               assessment_setup_snapshot_json,
               budget_snapshot_json,
               reserve_snapshot_json,
               appendix_manifest_snapshot_json
          FROM annual_packages
         WHERE id = ?
        """,
        (package_id,),
    ).fetchone()
    if row is None:
        return False
    status, a, b, r, m = row
    if status != "finalized":
        return False
    return all(v is not None for v in (a, b, r, m))


def resolve_compile_appendix_entries(
    *,
    property_id: int,
    package_id: Optional[int],
    connection: sqlite3.Connection,
) -> list[tuple[Path, str]]:
    """Resolve the appendix manifest into (path, display_title) pairs.

    Same resolution as :func:`resolve_compile_appendix_paths`, but keeps
    each entry's ``display_title`` (task: appendix TOC page numbers) so
    the compiler can label these appendices in the table of contents.
    """
    manifest = resolve_appendix_manifest(
        property_id=property_id, package_id=package_id, connection=connection,
    )
    entries: list[tuple[Path, str]] = []
    for entry in manifest:
        if appendix_file_exists(entry.file_id):
            entries.append((appendix_file_path(entry.file_id), entry.display_title))
    return entries


def resolve_compile_appendix_paths(
    *,
    property_id: int,
    package_id: Optional[int],
    connection: sqlite3.Connection,
) -> list[Path]:
    """Resolve the appendix manifest into filesystem paths for the merge.

    Walks :func:`appendix_manifest.resolve_appendix_manifest` and
    converts each ``file_id`` to an absolute path. Skips paths that
    don't exist on disk (matching the existing static-appendix
    skip-with-warning behavior); the caller decides whether a missing
    appendix should fail preflight.
    """
    entries = resolve_compile_appendix_entries(
        property_id=property_id, package_id=package_id, connection=connection,
    )
    return [path for path, _title in entries]


def compile_input_summary(
    *,
    property_id: int,
    package_id: Optional[int],
    connection: sqlite3.Connection,
) -> dict:
    """Build a debug-friendly summary of which branch the compile took.

    The audit log embeds this so a future re-render shows the operator
    which snapshots fired and which appendices resolved.
    """
    use_snapshots = should_use_snapshots(
        package_id=package_id, connection=connection,
    )
    manifest = resolve_appendix_manifest(
        property_id=property_id, package_id=package_id, connection=connection,
    )
    return {
        "package_id": package_id,
        "use_snapshots": use_snapshots,
        "appendix_count": len(manifest),
        "appendix_sources": sorted({entry.source for entry in manifest}),
        "appendix_titles": [entry.display_title for entry in manifest],
    }


__all__ = [
    "compile_input_summary",
    "resolve_compile_appendix_entries",
    "resolve_compile_appendix_paths",
    "should_use_snapshots",
]
