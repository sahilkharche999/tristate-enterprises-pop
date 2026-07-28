"""CC&R extraction-run approval service.

Extends the DRE approval flow for governing-document runs:
  1. Reads operator-entered per-unit factors from
     dre_extraction_runs.operator_unit_factors_json.
  2. Merges those factors into the extraction's unit_structure.units so
     populate_setup_children populates per-unit rows correctly.
  3. Blocks promotion (raises MissingUnitFactors) when a proportional
     pool has no factors from either extraction or operator entry — rather
     than distributing equally and silently producing wrong assessments.

Reuses approve_extraction_run from dre_approval_service for the
shared lifecycle steps (supersede prior setup, insert assessment_setups
row, carry-forward mappings, update run status).
"""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from pydantic import BaseModel

from app.dre_extraction.promotion import (
    MissingUnitFactors,
    apply_review_edits_to_extraction,
    check_missing_unit_factors,
    entity_keys_touched_by_edits,
    parse_extraction_payload,
    populate_setup_children,
)
from app.dre_extraction.schemas import DRESetupExtraction, UnitRow
from app.governing_doc_extraction.coherence import (
    IncoherentCcrExtraction,
    assert_ccr_allocation_coherent,
)
from app.services.dre_approval_service import (
    DREApprovalResponse,
    ExtractionRunAlreadyPromoted,
    ExtractionRunNotApprovable,
    ExtractionRunNotFound,
    ReopenRepromoteResponse,
    SetupTypeLiteral,
    _now_iso,
    reopen_and_repromote,
)
from app.services.assessment_budget_mapping_rule_service import (
    carry_forward_reusable_mapping_rules_across_setups,
    derive_rules_from_dre_extraction,
)
from app.services.budget_line_mapping_service import carry_forward_mappings_across_setups
from app.services.dre_review_service import list_review_edits


class CCRUnitFactor(BaseModel):
    """One operator-entered per-unit factor (stored in operator_unit_factors_json)."""

    unit_number: str
    square_feet: Optional[Decimal] = None
    ownership_percent: Optional[Decimal] = None


def save_operator_unit_factors(
    *,
    extraction_run_id: int,
    property_id: int,
    factors: list[CCRUnitFactor],
    connection: sqlite3.Connection,
) -> int:
    """Persist operator-entered per-unit factors for a CC&R extraction run.

    Overwrites the whole set atomically — the caller sends the full list.
    Returns the number of factor entries saved.
    """
    row = connection.execute(
        "SELECT id FROM dre_extraction_runs WHERE id = ? AND property_id = ?",
        (extraction_run_id, property_id),
    ).fetchone()
    if row is None:
        raise ExtractionRunNotFound(
            f"extraction_run_id={extraction_run_id} not found for property_id={property_id}"
        )

    factors_dict: dict[str, dict] = {}
    for f in factors:
        entry: dict[str, Any] = {}
        if f.square_feet is not None:
            entry["square_feet"] = str(f.square_feet)
        if f.ownership_percent is not None:
            entry["ownership_percent"] = str(f.ownership_percent)
        factors_dict[f.unit_number] = entry

    connection.execute(
        "UPDATE dre_extraction_runs SET operator_unit_factors_json = ? WHERE id = ?",
        (json.dumps(factors_dict), extraction_run_id),
    )
    return len(factors_dict)


def get_operator_unit_factors(
    *,
    extraction_run_id: int,
    connection: sqlite3.Connection,
) -> dict[str, dict]:
    """Fetch stored operator unit factors for a run. Returns {} when none set."""
    row = connection.execute(
        "SELECT operator_unit_factors_json FROM dre_extraction_runs WHERE id = ?",
        (extraction_run_id,),
    ).fetchone()
    if row is None or not row[0]:
        return {}
    try:
        result = json.loads(row[0])
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def merge_operator_factors(
    extraction: DRESetupExtraction,
    operator_factors: dict[str, dict],
) -> DRESetupExtraction:
    """Return a new DRESetupExtraction with operator factors merged into unit rows.

    If the extraction already has units (machine-readable from the CC&R),
    update their square_feet / ownership_percent where operator-entered values
    are present. If no units exist, build unit rows from the operator factors.
    """
    if not operator_factors:
        return extraction

    # Normalize BOTH sides of the key match (M6): keys on the existing units
    # and the operator-factor keys are stripped identically, so "101 " and
    # "101" resolve to the same unit instead of appending a phantom.
    existing_by_num = {
        str(u.unit_number).strip(): u for u in (extraction.unit_structure.units or [])
    }
    normalized_operator_keys = {str(k).strip() for k in operator_factors}

    merged_units: list[UnitRow] = []
    for unit_number, factor_entry in operator_factors.items():
        # String normalization + exact-key matching with case sensitivity (fix M6 phantom units)
        unit_number = str(unit_number).strip()
        existing = existing_by_num.get(unit_number)

        sq_ft_raw = factor_entry.get("square_feet")
        own_pct_raw = factor_entry.get("ownership_percent")

        def _dec(v: Any) -> Optional[Decimal]:
            if v is None:
                return None
            try:
                return Decimal(str(v))
            except InvalidOperation:
                return None

        sq_ft = _dec(sq_ft_raw)
        own_pct = _dec(own_pct_raw)

        if existing is not None:
            merged_units.append(
                existing.model_copy(
                    update={
                        "square_feet": sq_ft if sq_ft is not None else existing.square_feet,
                        "ownership_percent": (
                            own_pct if own_pct is not None else existing.ownership_percent
                        ),
                    }
                )
            )
        else:
            merged_units.append(
                UnitRow(
                    unit_number=unit_number,
                    square_feet=sq_ft,
                    ownership_percent=own_pct,
                    category="",
                    residential_commercial_flag="",
                    parking_flag="",
                    source_page=None,
                    confidence=0.0,
                    pool_factors=[],
                )
            )

    # Also include any existing units NOT covered by operator factors.
    # Compare against the normalized operator keys so a whitespace-only
    # difference doesn't re-add a unit that was already merged above (M6).
    for unit_num, unit in existing_by_num.items():
        if unit_num not in normalized_operator_keys:
            merged_units.append(unit)

    unit_structure = extraction.unit_structure.model_copy(
        update={"units": merged_units, "unit_count": len(merged_units)}
    )
    return extraction.model_copy(update={"unit_structure": unit_structure})


def approve_ccr_extraction_run(
    *,
    property_id: int,
    extraction_run_id: int,
    setup_type: SetupTypeLiteral,
    reviewed_by: Optional[str],
    connection: sqlite3.Connection,
) -> DREApprovalResponse:
    """Promote a CC&R extraction run into a live AssessmentSetup.

    Extends the DRE approval flow with:
      - Operator factor injection into unit_structure.units
      - Pre-promotion check that proportional pools have unit factors

    Raises:
        ExtractionRunNotFound: run doesn't exist for this property.
        ExtractionRunAlreadyPromoted: concurrent approve race (→ 409).
        ExtractionRunNotApprovable: run is rejected (→ 400).
        MissingUnitFactors: proportional pool has no unit factors (→ 422).
        IncoherentCcrExtraction: collapsed / incomplete allocation policy (→ 422).
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
    _run_id, review_status, promoted_at, promoted_setup_id, parsed_json_text = row

    if promoted_at is not None:
        raise ExtractionRunAlreadyPromoted(
            extraction_run_id=extraction_run_id,
            promoted_setup_id=promoted_setup_id,
        )
    if review_status == "rejected":
        raise ExtractionRunNotApprovable(
            f"extraction_run_id={extraction_run_id} is review_status='rejected'."
        )

    # Parse the extraction, apply queued Review Workbench edits, then merge
    # operator-entered per-unit factors on top (review edits patch first,
    # CC&R factors merge second — same layering as this pipeline already
    # uses for missing-data injection).
    extraction: Optional[DRESetupExtraction] = parse_extraction_payload(parsed_json_text)
    edited_entity_keys: frozenset[str] = frozenset()
    if extraction is not None:
        edits = list_review_edits(
            dre_extraction_run_id=extraction_run_id, connection=connection,
        )
        extraction = apply_review_edits_to_extraction(extraction, edits)
        edited_entity_keys = entity_keys_touched_by_edits(
            extraction, [edit.field_path for edit in edits]
        )

    operator_factors = get_operator_unit_factors(
        extraction_run_id=extraction_run_id, connection=connection
    )
    if extraction is not None and operator_factors:
        extraction = merge_operator_factors(extraction, operator_factors)

    # Block collapsed factor-table / multi-method policy after edits.
    assert_ccr_allocation_coherent(extraction)

    # Block promotion if any proportional pool has no unit data (3.3).
    if extraction is not None and setup_type == "per_unit":
        missing = check_missing_unit_factors(extraction)
        if missing:
            raise MissingUnitFactors(missing)

    # Supersede any prior approved setup.
    prior_setup = connection.execute(
        "SELECT id FROM assessment_setups WHERE property_id = ? AND status = 'approved' "
        "ORDER BY id DESC LIMIT 1",
        (property_id,),
    ).fetchone()
    prior_setup_id = prior_setup[0] if prior_setup else None

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

    snapshot_counts: dict = {"pools": 0, "groups": 0, "units": 0, "unit_pool_allocations": 0}
    if extraction is not None:
        snapshot_counts = populate_setup_children(
            setup_id=new_setup_id,
            setup_type=setup_type,
            extraction=extraction,
            connection=connection,
            edited_entity_keys=edited_entity_keys,
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
        derive_rules_from_dre_extraction(
            property_id=property_id,
            assessment_setup_id=new_setup_id,
            source_dre_extraction_run_id=extraction_run_id,
            extraction=extraction,
            connection=connection,
            commit=False,
        )

    # Update properties with unit count if known.
    if extraction is not None and extraction.unit_structure.unit_count:
        count = extraction.unit_structure.unit_count
        if isinstance(count, int) and count > 0:
            connection.execute(
                "UPDATE properties SET units = ? WHERE id = ?",
                (count, property_id),
            )

    connection.execute(
        "UPDATE properties SET default_assessment_setup_id = ? WHERE id = ?",
        (new_setup_id, property_id),
    )

    promoted_at_ts = _now_iso()
    # H5: atomic claim — identical to the DRE approve path. The early
    # ``promoted_at IS NULL`` read is a fast-path; this predicate is the
    # authoritative guard so two concurrent CC&R approves cannot both promote
    # (duplicated pools/units, double default-setup repoint). The loser rolls
    # back its half-built setup and gets a 409.
    cursor = connection.execute(
        """
        UPDATE dre_extraction_runs
           SET review_status = 'promoted',
               promoted_setup_id = ?,
               promoted_at = ?,
               reviewed_by = COALESCE(?, reviewed_by)
         WHERE id = ?
           AND promoted_at IS NULL
        """,
        (new_setup_id, promoted_at_ts, reviewed_by, extraction_run_id),
    )
    if cursor.rowcount == 0:
        connection.rollback()
        winner = connection.execute(
            "SELECT promoted_setup_id FROM dre_extraction_runs WHERE id = ?",
            (extraction_run_id,),
        ).fetchone()
        raise ExtractionRunAlreadyPromoted(
            extraction_run_id=extraction_run_id,
            promoted_setup_id=winner[0] if winner else None,
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


def reopen_and_repromote_ccr_run(
    *,
    property_id: int,
    extraction_run_id: int,
    setup_type: SetupTypeLiteral,
    reviewed_by: Optional[str],
    connection: sqlite3.Connection,
) -> ReopenRepromoteResponse:
    """CC&R-aware ``reopen_and_repromote``: layers operator per-unit
    factors on top of review edits, same as ``approve_ccr_extraction_run``,
    and re-enforces the missing-unit-factors guard on re-promotion.
    """

    def _apply_ccr_factors(extraction: DRESetupExtraction) -> DRESetupExtraction:
        # Merge CC&R operator factors, then re-enforce allocation coherence
        # so re-promote cannot bypass the same guard as first-time approve.
        # Missing-unit-factors still runs in reopen_and_repromote after this.
        operator_factors = get_operator_unit_factors(
            extraction_run_id=extraction_run_id, connection=connection
        )
        if operator_factors:
            extraction = merge_operator_factors(extraction, operator_factors)
        assert_ccr_allocation_coherent(extraction)
        return extraction

    return reopen_and_repromote(
        property_id=property_id,
        extraction_run_id=extraction_run_id,
        setup_type=setup_type,
        reviewed_by=reviewed_by,
        connection=connection,
        extra_extraction_transform=_apply_ccr_factors,
    )


__all__ = [
    "CCRUnitFactor",
    "MissingUnitFactors",
    "IncoherentCcrExtraction",
    "save_operator_unit_factors",
    "get_operator_unit_factors",
    "merge_operator_factors",
    "approve_ccr_extraction_run",
    "reopen_and_repromote_ccr_run",
]
