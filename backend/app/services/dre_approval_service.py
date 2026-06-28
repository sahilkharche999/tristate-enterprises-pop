"""DRE extraction-run approval service (Phase 4.2 + 5.9).

Promotes a reviewed ``DREExtractionRun`` into a live
``AssessmentSetup`` row in a single transaction. Implements the
concurrent-edit guard from Phase 5.9: a second concurrent Approve
click sees ``promoted_at IS NOT NULL`` and returns 409 instead of
creating a duplicate setup.

Flow:

    1. Verify the run exists, belongs to the property, and is in
       ``review_status='pending'`` (or 'approved' awaiting promotion).
       Refuse if ``promoted_at`` is already set.
    2. Mark any prior approved AssessmentSetup for this property as
       ``status='superseded'``.
    3. Insert a new ``assessment_setups`` row with the operator-chosen
       ``setup_type`` (defaulting to the extraction's recommendation
       when omitted) and ``status='approved'``.
    4. Update the run: ``review_status='promoted'``,
       ``promoted_setup_id=<new>``, ``promoted_at=now``, ``reviewed_by``.

Caller commits via the supplied raw connection.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel

from app.dre_extraction.promotion import (
    parse_extraction_payload,
    promote_extraction_to_setup,
)
from app.services.assessment_budget_mapping_rule_service import (
    carry_forward_reusable_mapping_rules_across_setups,
    derive_rules_from_dre_extraction,
)
from app.services.budget_line_mapping_service import (
    carry_forward_mappings_across_setups,
)


def _resolve_extracted_unit_count(parsed_json_text: str) -> Optional[int]:
    """Pull a positive unit count out of the extracted DRE setup.

    Source priority:
      1. ``document_metadata.total_units`` — the DRE's own declared total.
      2. ``len(unit_structure.units)`` — number of individual-unit rows.
      3. ``sum(group.unit_count)`` across all groups.

    Returns the first positive integer found, or ``None`` if no source
    yields a usable value.
    """
    try:
        parsed: Any = json.loads(parsed_json_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None

    doc_meta = parsed.get("document_metadata") or {}
    total = doc_meta.get("total_units")
    if isinstance(total, int) and total > 0:
        return total
    if isinstance(total, str) and total.strip().isdigit():
        v = int(total.strip())
        if v > 0:
            return v

    unit_structure = parsed.get("unit_structure") or {}
    units = unit_structure.get("units") or []
    if isinstance(units, list) and len(units) > 0:
        return len(units)

    groups = unit_structure.get("groups") or []
    if isinstance(groups, list):
        group_total = 0
        for g in groups:
            if not isinstance(g, dict):
                continue
            uc = g.get("unit_count")
            if isinstance(uc, int) and uc > 0:
                group_total += uc
            elif isinstance(uc, str) and uc.strip().isdigit():
                group_total += int(uc.strip())
        if group_total > 0:
            return group_total

    return None


SetupTypeLiteral = Literal["fixed", "grouped", "per_unit"]


class DREApprovalResponse(BaseModel):
    """JSON shape returned by the approval endpoint."""

    extraction_run_id: int
    promoted_setup_id: int
    setup_type: SetupTypeLiteral
    promoted_at: str
    reviewed_by: Optional[str]
    snapshot_counts: dict = {}


class ExtractionRunNotFound(LookupError):
    """Raised when the run doesn't exist or isn't tied to the property."""


class ExtractionRunAlreadyPromoted(RuntimeError):
    """Raised when a second concurrent Approve hits a run whose
    ``promoted_at`` is already set. The endpoint maps this to HTTP 409.
    """

    def __init__(self, extraction_run_id: int, promoted_setup_id: Optional[int]) -> None:
        self.extraction_run_id = extraction_run_id
        self.promoted_setup_id = promoted_setup_id
        super().__init__(
            f"DREExtractionRun id={extraction_run_id} is already promoted "
            f"(setup_id={promoted_setup_id}); refusing concurrent re-approval."
        )


class ExtractionRunNotApprovable(RuntimeError):
    """Raised when a run's ``review_status`` is ``rejected`` or any
    state that the approval endpoint refuses to promote.
    """


class ExtractionRunNotPromoted(RuntimeError):
    """Raised when a demote is requested on a run that was never
    promoted (``promoted_at IS NULL``). Endpoint maps this to HTTP 400.
    """


class SetupPinnedByFinalizedPackage(RuntimeError):
    """Raised when a demote would unseat an AssessmentSetup that a
    finalized AnnualPackage depends on. A finalized package is an
    immutable disclosure artifact, so demoting its setup is refused
    (HTTP 409) until the operator deals with the package first.
    """

    def __init__(self, setup_id: int, package_ids: list[int]) -> None:
        self.setup_id = setup_id
        self.package_ids = package_ids
        super().__init__(
            f"AssessmentSetup id={setup_id} is referenced by finalized "
            f"AnnualPackage(s) {package_ids}; cannot demote."
        )


class DREDemotionResponse(BaseModel):
    """JSON shape returned by the demote endpoint."""

    extraction_run_id: int
    demoted_setup_id: int
    restored_setup_id: Optional[int]
    review_status: str
    default_assessment_setup_id: Optional[int]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def approve_extraction_run(
    *,
    property_id: int,
    extraction_run_id: int,
    setup_type: SetupTypeLiteral,
    reviewed_by: Optional[str],
    connection: sqlite3.Connection,
) -> DREApprovalResponse:
    """Promote an extraction run into a new approved AssessmentSetup.

    Raises:
        ExtractionRunNotFound: when run doesn't exist for the property.
        ExtractionRunAlreadyPromoted: when ``promoted_at`` is set
            (concurrent-edit race). Endpoint maps to 409.
        ExtractionRunNotApprovable: when ``review_status='rejected'``.
    """
    if setup_type not in ("fixed", "grouped", "per_unit"):
        raise ValueError(
            f"Unknown setup_type {setup_type!r}; expected fixed | grouped | per_unit"
        )

    row = connection.execute(
        "SELECT id, review_status, promoted_at, promoted_setup_id, parsed_json "
        "FROM dre_extraction_runs WHERE id = ? AND property_id = ?",
        (extraction_run_id, property_id),
    ).fetchone()
    if row is None:
        raise ExtractionRunNotFound(
            f"extraction_run_id={extraction_run_id} not found for property_id={property_id}"
        )
    run_id_db, review_status, promoted_at, promoted_setup_id, parsed_json_text = row

    if promoted_at is not None:
        raise ExtractionRunAlreadyPromoted(
            extraction_run_id=run_id_db,
            promoted_setup_id=promoted_setup_id,
        )

    if review_status == "rejected":
        raise ExtractionRunNotApprovable(
            f"extraction_run_id={extraction_run_id} is review_status='rejected'; "
            "cannot promote a rejected run."
        )

    prior_setup = connection.execute(
        """
        SELECT id
          FROM assessment_setups
         WHERE property_id = ? AND status = 'approved'
         ORDER BY id DESC LIMIT 1
        """,
        (property_id,),
    ).fetchone()
    prior_setup_id = prior_setup[0] if prior_setup else None

    # Supersede any prior approved setup for this property
    connection.execute(
        "UPDATE assessment_setups SET status = 'superseded' "
        "WHERE property_id = ? AND status = 'approved'",
        (property_id,),
    )

    cur = connection.execute(
        """
        INSERT INTO assessment_setups (
            property_id, source_dre_document_id, setup_type, display_mode,
            reviewed_by, reviewed_at, approved_at, status
        ) VALUES (
            ?,
            (SELECT dre_document_id FROM dre_extraction_runs WHERE id = ?),
            ?, ?, ?, ?, ?, 'approved'
        )
        """,
        (
            property_id, extraction_run_id,
            setup_type, setup_type,
            reviewed_by, _now_iso(), _now_iso(),
        ),
    )
    new_setup_id = cur.lastrowid
    if new_setup_id is None:
        raise RuntimeError("sqlite did not return a lastrowid for assessment_setups")

    # Snapshot the extraction's parsed_json into AllocationPool / Group /
    # Unit / UnitPoolAllocation rows so the engine has recipients and
    # pools to allocate against. Best-effort: bad rows are skipped with
    # a logger warning so a partially-extracted DRE still produces a
    # usable setup (the operator can clean up in the Review Workbench).
    snapshot_counts = promote_extraction_to_setup(
        setup_id=new_setup_id,
        setup_type=setup_type,
        parsed_json_text=parsed_json_text,
        connection=connection,
    )
    if prior_setup_id is not None:
        carry_forward_mappings_across_setups(
            property_id=property_id,
            old_setup_id=prior_setup_id,
            new_setup_id=new_setup_id,
            connection=connection,
            commit=False,
        )
        carry_forward_reusable_mapping_rules_across_setups(
            property_id=property_id,
            old_setup_id=prior_setup_id,
            new_setup_id=new_setup_id,
            connection=connection,
            commit=False,
        )

    extraction = parse_extraction_payload(parsed_json_text)
    if extraction is not None:
        derive_rules_from_dre_extraction(
            property_id=property_id,
            assessment_setup_id=new_setup_id,
            source_dre_extraction_run_id=extraction_run_id,
            extraction=extraction,
            connection=connection,
            commit=False,
        )

    # Set the property's default_assessment_setup_id so future budget
    # cycles know which setup to use without re-querying status='approved'.
    connection.execute(
        "UPDATE properties SET default_assessment_setup_id = ? WHERE id = ?",
        (new_setup_id, property_id),
    )

    # Write the extracted unit count back to ``properties.units`` so the HOA
    # (created with units=0, "Pending DRE") finally reports a real number.
    # Source priority: document_metadata.total_units (the DRE's own declared
    # total) → len(unit_structure.units) → sum(group.unit_count). Only update
    # if we have a positive value; never overwrite an existing real count
    # with zero in case the extraction missed it.
    extracted_units = _resolve_extracted_unit_count(parsed_json_text)
    if extracted_units and extracted_units > 0:
        connection.execute(
            "UPDATE properties SET units = ? WHERE id = ?",
            (extracted_units, property_id),
        )

    promoted_at_ts = _now_iso()
    connection.execute(
        """
        UPDATE dre_extraction_runs
           SET review_status = 'promoted',
               promoted_setup_id = ?,
               promoted_at = ?,
               reviewed_by = COALESCE(?, reviewed_by)
         WHERE id = ?
        """,
        (new_setup_id, promoted_at_ts, reviewed_by, extraction_run_id),
    )

    connection.commit()
    return DREApprovalResponse(
        extraction_run_id=extraction_run_id,
        promoted_setup_id=new_setup_id,
        setup_type=setup_type,
        promoted_at=promoted_at_ts,
        reviewed_by=reviewed_by,
        snapshot_counts=snapshot_counts,
    )


def demote_extraction_run(
    *,
    property_id: int,
    extraction_run_id: int,
    reviewed_by: Optional[str],
    connection: sqlite3.Connection,
) -> DREDemotionResponse:
    """Reverse a promotion: unseat a wrongly-promoted extraction run.

    This is the inverse of :func:`approve_extraction_run`. It works for
    both DRE and CC&R runs (the lifecycle is ``document_type``-agnostic):

      1. Mark the promoted ``AssessmentSetup`` ``status='superseded'``.
      2. Restore the setup this promotion superseded (the most-recent
         ``superseded`` setup with a lower id) back to ``status='approved'``
         and repoint ``properties.default_assessment_setup_id`` to it.
         If there is no prior setup, clear the default pointer so the
         property has no live setup — leaving a clean slate for promoting
         a different document (e.g. switching from a wrong DRE to a CC&R).
      3. Revert the run to ``review_status='approved'`` (still reviewed,
         no longer promoted) and clear ``promoted_setup_id`` / ``promoted_at``
         so the UI offers re-promote.

    The promoted setup is **superseded, not deleted**, so its child pool /
    group / unit rows and any ``annual_packages`` / merge-application
    pointers (``ON DELETE SET NULL``) remain valid — demotion is
    non-destructive and reversible by re-promoting.

    Raises:
        ExtractionRunNotFound: run doesn't exist for the property.
        ExtractionRunNotPromoted: run was never promoted.
        SetupPinnedByFinalizedPackage: a finalized AnnualPackage depends
            on the setup; endpoint maps to HTTP 409.
    """
    row = connection.execute(
        "SELECT id, review_status, promoted_at, promoted_setup_id "
        "FROM dre_extraction_runs WHERE id = ? AND property_id = ?",
        (extraction_run_id, property_id),
    ).fetchone()
    if row is None:
        raise ExtractionRunNotFound(
            f"extraction_run_id={extraction_run_id} not found for property_id={property_id}"
        )
    _run_id, review_status, promoted_at, promoted_setup_id = row

    if promoted_at is None or promoted_setup_id is None:
        raise ExtractionRunNotPromoted(
            f"extraction_run_id={extraction_run_id} is not promoted "
            f"(review_status={review_status!r}); nothing to demote."
        )

    # Guard: refuse if a finalized AnnualPackage pins this setup. Finalized
    # packages are immutable disclosure artifacts; the operator must resolve
    # them before the underlying setup can be unseated.
    finalized = connection.execute(
        "SELECT id FROM annual_packages "
        "WHERE assessment_setup_id = ? AND status = 'finalized'",
        (promoted_setup_id,),
    ).fetchall()
    if finalized:
        raise SetupPinnedByFinalizedPackage(
            setup_id=promoted_setup_id,
            package_ids=[r[0] for r in finalized],
        )

    # 1. Supersede the wrongly-promoted setup.
    connection.execute(
        "UPDATE assessment_setups SET status = 'superseded', updated_at = ? "
        "WHERE id = ? AND property_id = ?",
        (_now_iso(), promoted_setup_id, property_id),
    )

    # 2. Restore the setup this promotion superseded, if any. The inverse of
    #    promote's "supersede prior approved" step: pick the highest-id
    #    superseded setup below the demoted one — the one that was live
    #    immediately before this promotion.
    prior = connection.execute(
        """
        SELECT id FROM assessment_setups
         WHERE property_id = ? AND status = 'superseded' AND id < ?
         ORDER BY id DESC LIMIT 1
        """,
        (property_id, promoted_setup_id),
    ).fetchone()
    restored_setup_id = prior[0] if prior else None

    if restored_setup_id is not None:
        connection.execute(
            "UPDATE assessment_setups SET status = 'approved', updated_at = ? "
            "WHERE id = ?",
            (_now_iso(), restored_setup_id),
        )
    connection.execute(
        "UPDATE properties SET default_assessment_setup_id = ? WHERE id = ?",
        (restored_setup_id, property_id),
    )

    # 3. Revert the run to a reviewed-but-not-promoted state.
    connection.execute(
        """
        UPDATE dre_extraction_runs
           SET review_status = 'approved',
               promoted_setup_id = NULL,
               promoted_at = NULL,
               reviewed_by = COALESCE(?, reviewed_by)
         WHERE id = ?
        """,
        (reviewed_by, extraction_run_id),
    )

    connection.commit()
    return DREDemotionResponse(
        extraction_run_id=extraction_run_id,
        demoted_setup_id=promoted_setup_id,
        restored_setup_id=restored_setup_id,
        review_status="approved",
        default_assessment_setup_id=restored_setup_id,
    )
