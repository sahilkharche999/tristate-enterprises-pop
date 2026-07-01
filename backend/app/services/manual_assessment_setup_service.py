"""Manual assessment setup entry — no DRE/CC&R extraction run at all.

Some HOAs (small associations, legacy paper records) have no formal DRE
or governing document to extract from. This module lets an operator
build the pools/groups/units directly and land them in a synthetic
``dre_extraction_runs`` row that looks, to every downstream consumer,
like any other extraction run.

No new promotion path: the resulting run is approved through the
existing ``approve_extraction_run`` (or ``approve_ccr_extraction_run``
for a per_unit setup that needs the proportional-pool guard) exactly
like a Gemini-derived run — including the same ``MissingUnitFactors``-
equivalent protection, since ``populate_setup_children`` doesn't care
where the extraction came from.

Distinguished from a real Gemini run by ``model_name = 'manual'``
(rather than a new ``extraction_method`` column — this reuses existing
``dre_extraction_runs`` columns per the change's migration plan, which
adds no new tables/columns).
"""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel

from app.dre_extraction.schemas import (
    AllocationPoolBlock,
    AssessmentSetupBlock,
    DocumentMetadata,
    DRESetupExtraction,
    GroupRow,
    PromptAllocationMethod,
    PromptSetupType,
    UnitRow,
    UnitStructure,
)


MANUAL_ENTRY_MODEL_NAME = "manual"


class PropertyNotFound(LookupError):
    """Raised when the property doesn't exist."""


class ManualPoolEntry(BaseModel):
    pool_key: str
    pool_name: str = ""
    annual_amount: Optional[Decimal] = None
    allocation_method: PromptAllocationMethod
    recipient_scope: str = "all_units"
    denominator_value: Optional[Decimal] = None
    variable_flag: bool = False


class ManualGroupEntry(BaseModel):
    group_id: str = ""
    label: str = ""
    unit_count: int
    average_square_feet: Optional[Decimal] = None
    ownership_percent: Optional[Decimal] = None


class ManualUnitEntry(BaseModel):
    unit_number: str
    square_feet: Optional[Decimal] = None
    ownership_percent: Optional[Decimal] = None
    category: str = ""
    parking_spaces: int = 0


class ManualExtractionRunResponse(BaseModel):
    dre_document_id: int
    extraction_run_id: int
    property_id: int


def _build_manual_extraction(
    *,
    setup_type: PromptSetupType,
    association_name: str,
    pools: list[ManualPoolEntry],
    groups: list[ManualGroupEntry],
    units: list[ManualUnitEntry],
) -> DRESetupExtraction:
    """Build a ``DRESetupExtraction`` from operator input.

    Every field the AI pipeline would attach as evidence (``source_pages``,
    citations) is absent by construction; ``confidence=1.0`` marks these as
    operator-asserted rather than a fabricated extraction score.
    """
    return DRESetupExtraction(
        document_metadata=DocumentMetadata(
            association_name=association_name, confidence=1.0,
        ),
        assessment_setup=AssessmentSetupBlock(
            setup_type=setup_type, confidence=1.0,
        ),
        unit_structure=UnitStructure(
            unit_count=len(units) or sum(g.unit_count for g in groups),
            group_count=len(groups),
            groups=[
                GroupRow(
                    group_id=g.group_id,
                    label=g.label,
                    unit_count=g.unit_count,
                    average_square_feet=g.average_square_feet,
                    ownership_percent=g.ownership_percent,
                    confidence=1.0,
                )
                for g in groups
            ],
            units=[
                UnitRow(
                    unit_number=u.unit_number,
                    square_feet=u.square_feet,
                    ownership_percent=u.ownership_percent,
                    category=u.category,
                    parking_flag=str(u.parking_spaces),
                    confidence=1.0,
                )
                for u in units
            ],
        ),
        allocation_pools=[
            AllocationPoolBlock(
                pool_key=p.pool_key,
                pool_name=p.pool_name or p.pool_key,
                annual_amount=p.annual_amount,
                allocation_method=p.allocation_method,
                recipient_scope=p.recipient_scope,
                denominator_value=p.denominator_value,
                variable_flag=p.variable_flag,
                confidence=1.0,
            )
            for p in pools
        ],
    )


def create_manual_extraction_run(
    *,
    property_id: int,
    setup_type: PromptSetupType,
    pools: list[ManualPoolEntry],
    groups: list[ManualGroupEntry],
    units: list[ManualUnitEntry],
    created_by: Optional[str],
    connection: sqlite3.Connection,
) -> ManualExtractionRunResponse:
    """Create a placeholder document + a ``dre_extraction_runs`` row whose
    ``parsed_json`` is built directly from operator input, with no Gemini
    call. Returns the new run id so the caller drives it through the
    identical review → approve pipeline any other run uses.
    """
    prop = connection.execute(
        "SELECT id FROM properties WHERE id = ?", (property_id,),
    ).fetchone()
    if prop is None:
        raise PropertyNotFound(f"property_id={property_id} not found")

    extraction = _build_manual_extraction(
        setup_type=setup_type,
        association_name="",
        pools=pools,
        groups=groups,
        units=units,
    )

    doc_cur = connection.execute(
        """
        INSERT INTO dre_documents (
            property_id, file_id, file_name, status, uploaded_by, document_type
        ) VALUES (?, ?, ?, 'active', ?, 'dre')
        """,
        (
            property_id,
            f"manual/{property_id}/{setup_type}",
            "Manually entered assessment setup",
            created_by,
        ),
    )
    document_id = doc_cur.lastrowid
    if document_id is None:
        raise RuntimeError("sqlite did not return a lastrowid for dre_documents")

    run_cur = connection.execute(
        """
        INSERT INTO dre_extraction_runs (
            dre_document_id, property_id, model_name, prompt_version,
            prompt_sha256, parsed_json, status, review_status
        ) VALUES (?, ?, ?, 'manual', 'manual', ?, 'succeeded', 'pending')
        """,
        (
            document_id, property_id, MANUAL_ENTRY_MODEL_NAME,
            json.dumps(extraction.model_dump(mode="json")),
        ),
    )
    run_id = run_cur.lastrowid
    if run_id is None:
        raise RuntimeError("sqlite did not return a lastrowid for dre_extraction_runs")

    connection.commit()
    return ManualExtractionRunResponse(
        dre_document_id=document_id,
        extraction_run_id=run_id,
        property_id=property_id,
    )


__all__ = [
    "MANUAL_ENTRY_MODEL_NAME",
    "ManualExtractionRunResponse",
    "ManualGroupEntry",
    "ManualPoolEntry",
    "ManualUnitEntry",
    "PropertyNotFound",
    "create_manual_extraction_run",
]
