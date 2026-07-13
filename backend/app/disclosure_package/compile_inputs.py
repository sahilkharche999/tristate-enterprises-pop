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

import json
import sqlite3
from pathlib import Path
from typing import Optional

from .appendix_manifest import ResolvedAppendix, resolve_appendix_manifest
from .appendix_storage import appendix_file_path, appendix_file_exists
from .schemas import PreflightError


def _is_stub_snapshot(raw_json: Optional[str]) -> bool:
    """True when a snapshot column holds no usable payload.

    Legacy packages were finalized with client-sent empty stubs (``{}``
    for all four columns), so 'non-null' is not a sufficient validity
    test — a stub column must read as "no snapshot" and route the render
    to the live branch WITH a loud warning, not silently render an empty
    package.

    An empty LIST is NOT a stub: the server-side assembler emits lists
    for collections (e.g. an appendix manifest for an HOA with no
    appendix documents is legitimately ``[]``). Only null / unparseable /
    empty-dict columns read as stubs — and the new assembler can emit
    none of those (`freeze` injects ``assessment_mode`` into dict
    payloads, and budget/reserve/compile_context are non-empty by
    construction).
    """
    if raw_json is None:
        return True
    try:
        value = json.loads(raw_json)
    except (TypeError, ValueError):
        return True
    if value is None:
        return True
    if isinstance(value, dict) and not value:
        return True
    return False


def should_use_snapshots(
    *,
    package_id: Optional[int],
    connection: sqlite3.Connection,
) -> bool:
    """Return True when the compile MUST load from frozen snapshots.

    True when ``annual_packages.status='finalized'`` and all five
    snapshot columns (four Phase-4.8 columns + ``compile_context``) hold
    real payloads — non-null AND non-stub (see :func:`_is_stub_snapshot`;
    legacy packages froze client-sent ``{}`` stubs). False otherwise:
    draft/approved/rendered packages, ad-hoc previews
    (``package_id=None``), and stub-finalized legacy packages all read
    live state — the caller is responsible for surfacing the legacy-stub
    warning.
    """
    if package_id is None:
        return False
    row = connection.execute(
        """
        SELECT status,
               assessment_setup_snapshot_json,
               budget_snapshot_json,
               reserve_snapshot_json,
               appendix_manifest_snapshot_json,
               compile_context_snapshot_json
          FROM annual_packages
         WHERE id = ?
        """,
        (package_id,),
    ).fetchone()
    if row is None:
        return False
    status, *snapshot_columns = row
    if status != "finalized":
        return False
    return not any(_is_stub_snapshot(v) for v in snapshot_columns)


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
    missing: list[PreflightError] = []
    for entry in manifest:
        if appendix_file_exists(entry.file_id):
            entries.append((appendix_file_path(entry.file_id), entry.display_title))
        else:
            # H7: the operator selected this appendix, but its file is gone
            # from storage. NEVER silently drop it — the shipped PDF would
            # quietly shrink with the TOC renumbered around the gap. Fail
            # with the file named and both in-app fixes offered.
            missing.append(
                PreflightError(
                    field_path=f"appendix_manifest.{entry.file_id}",
                    message=(
                        f"Selected appendix '{entry.display_title}' "
                        f"({entry.file_name}) is missing from storage."
                    ),
                    severity="blocking",
                    code="appendix_file_missing",
                    affected_value=entry.file_id,
                    suggested_fix=(
                        "Re-upload the file, or deselect this appendix from the "
                        "package's appendix set, then generate again."
                    ),
                )
            )
    if missing:
        # Imported lazily to keep the compiler↔compile_inputs boundary clean.
        from .compiler import CompileError

        raise CompileError(
            f"Preflight blocked compilation: {len(missing)} missing appendix "
            "file(s)",
            errors=missing,
        )
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
