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
import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, Field

from app.dre_extraction.promotion import (
    AmbiguousOwnershipPercentForm,
    EditedEntityFailedToPromote,
    MissingUnitFactors,
    InvalidStructuralOperation,
    StaleStructuralOperation,
    apply_review_edits_to_extraction,
    check_missing_unit_factors,
    derive_ccr_pool_treatments,
    entity_keys_touched_by_edits,
    normalize_extraction_for_promotion,
    parse_extraction_payload,
    populate_setup_children,
    validate_edited_entities_for_promotion,
    validate_ownership_percent_form,
    validate_specified_value_pools,
)
from app.dre_extraction.schemas import DRESetupExtraction, UnitPoolFactor, UnitRow
from app.governing_doc_extraction.coherence import (
    IncoherentCcrExtraction,
    assess_allocation_coherence,
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
    fixed_amounts: dict[str, Annotated[Decimal, Field(gt=0)]] = Field(
        default_factory=dict
    )
    custom_factors: dict[str, Annotated[Decimal, Field(gt=0)]] = Field(
        default_factory=dict
    )


class IncompleteOperatorUnitRoster(ValueError):
    """Operator rows cannot safely replace an incomplete extracted roster."""


def _validate_replacement_roster(
    extraction: DRESetupExtraction,
    unit_numbers: list[str],
) -> None:
    expected = extraction.unit_structure.unit_count
    if expected is None or expected <= 0:
        return

    normalized = [str(value).strip() for value in unit_numbers]
    unique = set(normalized)
    if (
        any(not value for value in normalized)
        or len(unique) != len(normalized)
        or len(normalized) != expected
    ):
        raise IncompleteOperatorUnitRoster(
            f"Enter all {expected} distinct homes before saving this roster."
        )


class CCRPromotionIssue(BaseModel):
    code: str
    severity: Literal["warning", "error"]
    category_key: Optional[str] = None
    source_pages: list[int] = Field(default_factory=list)
    explanation: str
    recommended_operation: Optional[dict[str, Any]] = None
    approval_blocked: bool


class CCRPromotionPreview(BaseModel):
    extraction_run_id: int
    review_version: int
    resolved_extraction: Optional[DRESetupExtraction]
    issues: list[CCRPromotionIssue] = Field(default_factory=list)
    approval_blocked: bool


class PromotionInputsChanged(RuntimeError):
    """Review edits or operator factors changed after resolution."""

    def __init__(self, extraction_run_id: int) -> None:
        self.extraction_run_id = extraction_run_id
        super().__init__(
            "CC&R corrections changed while approval was starting. "
            "Refresh the preview and approve the latest version."
        )


@dataclass(frozen=True)
class ResolvedCCRPromotion:
    preview: CCRPromotionPreview
    edited_entity_keys: frozenset[str]
    ownership_ambiguity: Optional[AmbiguousOwnershipPercentForm] = None
    landing_failures: tuple[str, ...] = ()
    review_row_count: int = 0
    review_max_id: int = 0
    operator_factors_raw: Optional[str] = None


def _pool_for_key(
    extraction: Optional[DRESetupExtraction], category_key: Optional[str]
) -> Optional[Any]:
    if extraction is None or category_key is None:
        return None
    return next(
        (pool for pool in extraction.allocation_pools if pool.pool_key == category_key),
        None,
    )


def _coherence_issue(
    reason: str, extraction: DRESetupExtraction
) -> CCRPromotionIssue:
    quoted_keys = re.findall(r"\b(?:pool|category)\s+'([^']+)'", reason)
    category_key = quoted_keys[0] if quoted_keys else None
    pool = _pool_for_key(extraction, category_key)
    if "must use" in reason and "billing cadence" in reason:
        code = "CCR_BILLING_COMBINATION_UNSUPPORTED"
        explanation = (
            f"Category '{category_key}' has a billing schedule that does not "
            "match its charge type. Regular dues, cost centers, and reserve "
            "contributions recur; separately billed special assessments are one time."
        )
        recommended = {
            "operation": "update",
            "category_key": category_key,
            "changes": {},
        }
    elif "has no source page citation" in reason:
        code = "CCR_POOL_SOURCE_MISSING"
        explanation = (
            f"Choose the CC&R page or pages that support category "
            f"'{category_key}' before approval."
        )
        recommended = {
            "operation": "update",
            "category_key": category_key,
            "changes": {"source_pages": []},
        }
    elif "declared allocation context" in reason:
        code = "CCR_DECLARED_CATEGORY_MISSING"
        explanation = (
            "The document declares an assessment category that has not yet "
            "been represented by a reviewed allocation pool."
        )
        recommended = {"operation": "add"}
    elif "does not exclude exception pools" in reason:
        code = "CCR_RESIDUAL_EXCLUSIONS_INCOMPLETE"
        explanation = (
            f"Category '{category_key}' must list every recurring exception "
            "category before approval."
        )
        recommended = {
            "operation": "update",
            "category_key": category_key,
            "changes": {"residual_after_pool_keys": []},
        }
    else:
        code = "CCR_ALLOCATION_STRUCTURE_INCOHERENT"
        explanation = (
            "The corrected categories still do not form a complete, internally "
            "consistent allocation policy."
        )
        recommended = (
            {"operation": "update", "category_key": category_key, "changes": {}}
            if category_key
            else None
        )
    return CCRPromotionIssue(
        code=code,
        severity="error",
        category_key=category_key,
        source_pages=list(pool.source_pages) if pool is not None else [],
        explanation=explanation,
        recommended_operation=recommended,
        approval_blocked=True,
    )


def resolve_ccr_promotion(
    *,
    property_id: int,
    extraction_run_id: int,
    setup_type: SetupTypeLiteral,
    connection: sqlite3.Connection,
) -> ResolvedCCRPromotion:
    """Resolve the immutable CC&R snapshot through the promotion gates.

    This function performs reads only. Approval, re-promotion, and preview all
    consume this exact result before any setup rows are written.
    """
    row = connection.execute(
        "SELECT parsed_json, operator_unit_factors_json "
        "FROM dre_extraction_runs "
        "WHERE id = ? AND property_id = ? "
        "AND COALESCE(document_type, 'dre') = 'ccr'",
        (extraction_run_id, property_id),
    ).fetchone()
    if row is None:
        raise ExtractionRunNotFound(
            f"extraction_run_id={extraction_run_id} not found for property_id={property_id}"
        )

    edits = list_review_edits(
        dre_extraction_run_id=extraction_run_id, connection=connection
    )
    review_row_count = len(edits)
    review_max_id = max((edit.edit_id for edit in edits), default=0)
    review_version = sum(
        edit.field_path == "allocation_pools.$operation" for edit in edits
    )
    extraction = parse_extraction_payload(row[0])
    operator_factors_raw = row[1]
    issues: list[CCRPromotionIssue] = []
    edited_entity_keys: frozenset[str] = frozenset()
    ownership_ambiguity: Optional[AmbiguousOwnershipPercentForm] = None
    landing_failures: tuple[str, ...] = ()

    if extraction is None:
        issues.append(
            CCRPromotionIssue(
                code="CCR_EXTRACTION_INVALID",
                severity="error",
                explanation=(
                    "The saved CC&R extraction could not be read. Re-run extraction "
                    "before attempting approval."
                ),
                approval_blocked=True,
            )
        )
    else:
        try:
            extraction = apply_review_edits_to_extraction(extraction, edits)
            edited_entity_keys = entity_keys_touched_by_edits(
                extraction, edits
            )
        except StaleStructuralOperation:
            issues.append(
                CCRPromotionIssue(
                    code="CCR_OPERATION_VERSION_STALE",
                    severity="error",
                    explanation=(
                        "A saved category correction was based on an older version. "
                        "Refresh the review and apply that correction again."
                    ),
                    approval_blocked=True,
                )
            )
        except InvalidStructuralOperation as exc:
            category_key = exc.category_keys[0] if exc.category_keys else None
            issues.append(
                CCRPromotionIssue(
                    code="CCR_OPERATION_INVALID",
                    severity="error",
                    category_key=category_key,
                    explanation=(
                        "A saved category correction can no longer be applied safely. "
                        "Refresh the review and enter the correction again."
                    ),
                    approval_blocked=True,
                )
            )
        except Exception as exc:
            from app.dre_extraction.promotion import UnresolvableReviewEdit

            if not isinstance(exc, UnresolvableReviewEdit):
                raise
            issues.append(
                CCRPromotionIssue(
                    code="CCR_REVIEW_EDIT_UNRESOLVABLE",
                    severity="error",
                    explanation=(
                        "A saved field correction no longer matches this extraction. "
                        "Refresh the review and enter the correction again."
                    ),
                    approval_blocked=True,
                )
            )

    if extraction is not None and not issues:
        operator_factors = parse_operator_unit_factors(operator_factors_raw)
        try:
            extraction = merge_operator_factors(extraction, operator_factors)
            edited_entity_keys = edited_entity_keys.union(
                f"unit:{str(unit_number).strip()}"
                for unit_number in operator_factors
            )
        except IncompleteOperatorUnitRoster as exc:
            issues.append(
                CCRPromotionIssue(
                    code="CCR_OPERATOR_ROSTER_INCOMPLETE",
                    severity="error",
                    explanation=(
                        f"The saved home-value list is incomplete. {exc} "
                        "The existing values were kept; replace the full list to continue."
                    ),
                    approval_blocked=True,
                )
            )
        extraction = derive_ccr_pool_treatments(extraction)
        extraction = normalize_extraction_for_promotion(extraction)

        landing_failures = tuple(
            validate_edited_entities_for_promotion(
                extraction,
                setup_type=setup_type,
                edited_entity_keys=edited_entity_keys,
            )
        )
        for entity_ref in landing_failures:
            entity_type, entity_key = entity_ref.split(":", 1)
            source_pages: list[int] = []
            if entity_type == "pool":
                pool = _pool_for_key(extraction, entity_key)
                source_pages = list(pool.source_pages) if pool is not None else []
            elif entity_type == "group":
                group = next(
                    (
                        group
                        for index, group in enumerate(extraction.unit_structure.groups)
                        if (group.group_id or group.label or str(index)) == entity_key
                    ),
                    None,
                )
                source_pages = (
                    [group.source_page]
                    if group is not None and group.source_page is not None
                    else []
                )
            else:
                unit = next(
                    (
                        unit
                        for unit in extraction.unit_structure.units
                        if unit.unit_number == entity_key
                    ),
                    None,
                )
                source_pages = (
                    [unit.source_page]
                    if unit is not None and unit.source_page is not None
                    else []
                )
            issues.append(
                CCRPromotionIssue(
                    code="CCR_EDITED_ENTITY_UNPROMOTABLE",
                    severity="error",
                    category_key=entity_key,
                    source_pages=source_pages,
                    explanation=(
                        f"The corrected {entity_type} '{entity_key}' still contains "
                        "a value that cannot be promoted. Correct that category "
                        "before approval."
                    ),
                    recommended_operation=(
                        {
                            "operation": "update",
                            "category_key": entity_key,
                            "changes": {},
                        }
                        if entity_type == "pool"
                        else None
                    ),
                    approval_blocked=True,
                )
            )

        finding = assess_allocation_coherence(extraction)
        issues.extend(_coherence_issue(reason, extraction) for reason in finding.reasons)

        if setup_type != "per_unit":
            for pool in extraction.allocation_pools:
                if (
                    pool.recipient_scope == "all_units"
                    and pool.allocation_method
                    not in {
                        "square_footage",
                        "ownership_percentage",
                        "custom_factor",
                        "specified_value",
                    }
                ):
                    continue
                issues.append(
                    CCRPromotionIssue(
                        code="CCR_SETUP_TYPE_INCOMPATIBLE",
                        severity="error",
                        category_key=pool.pool_key,
                        source_pages=list(pool.source_pages),
                        explanation=(
                            f"Category '{pool.pool_key}' assigns values to each home "
                            "or to selected homes. Choose the per-home setup before approval."
                        ),
                        approval_blocked=True,
                    )
                )

        try:
            validate_ownership_percent_form(extraction)
        except AmbiguousOwnershipPercentForm as exc:
            ownership_ambiguity = exc
            for pool in extraction.allocation_pools:
                if pool.allocation_method != "ownership_percentage":
                    continue
                issues.append(
                    CCRPromotionIssue(
                        code="CCR_OWNERSHIP_PERCENT_AMBIGUOUS",
                        severity="error",
                        category_key=pool.pool_key,
                        source_pages=list(pool.source_pages),
                        explanation=(
                            f"Category '{pool.pool_key}' uses ownership percentages, "
                            "but the saved values do not clearly indicate whether they "
                            "are fractions or percentage points. Choose the printed "
                            "format before approval."
                        ),
                        recommended_operation={
                            "operation": "set_ownership_percent_form",
                            "allowed_values": ["fraction", "points"],
                        },
                        approval_blocked=True,
                    )
                )

        if setup_type == "per_unit":
            for category_key in check_missing_unit_factors(extraction):
                pool = _pool_for_key(extraction, category_key)
                issues.append(
                    CCRPromotionIssue(
                        code="CCR_UNIT_FACTORS_MISSING",
                        severity="error",
                        category_key=category_key,
                        source_pages=list(pool.source_pages) if pool is not None else [],
                        explanation=(
                            f"Category '{category_key}' uses a proportional method, "
                            "but at least one participating home is missing a "
                            "positive allocation factor."
                        ),
                        recommended_operation=None,
                        approval_blocked=True,
                    )
                )
            for category_key, validation in validate_specified_value_pools(
                extraction
            ).items():
                if validation.valid:
                    continue
                pool = _pool_for_key(extraction, category_key)
                missing = validation.failure_kind == "missing"
                issues.append(
                    CCRPromotionIssue(
                        code=(
                            "CCR_SPECIFIED_VALUES_MISSING"
                            if missing
                            else "CCR_SPECIFIED_VALUES_INVALID"
                        ),
                        severity="error",
                        category_key=category_key,
                        source_pages=list(pool.source_pages) if pool is not None else [],
                        explanation=(
                            (
                                f"Category '{category_key}' needs a positive dollar "
                                "amount for every participating home before approval."
                            )
                            if missing
                            else (
                                f"Category '{category_key}' has per-home amounts that "
                                f"do not reconcile to its documented total: "
                                f"{validation.reason}."
                            )
                        ),
                        approval_blocked=True,
                    )
                )

    preview = CCRPromotionPreview(
        extraction_run_id=extraction_run_id,
        review_version=review_version,
        resolved_extraction=extraction,
        issues=issues,
        approval_blocked=any(issue.approval_blocked for issue in issues),
    )
    return ResolvedCCRPromotion(
        preview=preview,
        edited_entity_keys=edited_entity_keys,
        ownership_ambiguity=ownership_ambiguity,
        landing_failures=landing_failures,
        review_row_count=review_row_count,
        review_max_id=review_max_id,
        operator_factors_raw=operator_factors_raw,
    )


def _pin_ccr_promotion_inputs(
    *,
    property_id: int,
    extraction_run_id: int,
    resolved: ResolvedCCRPromotion,
    connection: sqlite3.Connection,
) -> None:
    """Acquire SQLite's write lock iff the resolved review/factor inputs match."""
    cursor = connection.execute(
        """
        UPDATE dre_extraction_runs
           SET id = id
         WHERE id = ?
           AND property_id = ?
           AND COALESCE(document_type, 'dre') = 'ccr'
           AND operator_unit_factors_json IS ?
           AND (
                SELECT COUNT(*)
                  FROM dre_review_edits
                 WHERE dre_extraction_run_id = ?
           ) = ?
           AND COALESCE((
                SELECT MAX(id)
                  FROM dre_review_edits
                 WHERE dre_extraction_run_id = ?
           ), 0) = ?
        """,
        (
            extraction_run_id,
            property_id,
            resolved.operator_factors_raw,
            extraction_run_id,
            resolved.review_row_count,
            extraction_run_id,
            resolved.review_max_id,
        ),
    )
    if cursor.rowcount != 1:
        raise PromotionInputsChanged(extraction_run_id)


def _raise_preview_blockers(resolved: ResolvedCCRPromotion) -> None:
    preview = resolved.preview
    if not preview.approval_blocked:
        return
    issue_dicts = [issue.model_dump(mode="json") for issue in preview.issues]
    codes = {issue.code for issue in preview.issues}
    if "CCR_UNIT_FACTORS_MISSING" in codes:
        exc: Exception = MissingUnitFactors(
            [
                issue.category_key
                for issue in preview.issues
                if issue.code == "CCR_UNIT_FACTORS_MISSING"
                and issue.category_key is not None
            ]
        )
    elif resolved.ownership_ambiguity is not None:
        exc = resolved.ownership_ambiguity
    elif resolved.landing_failures:
        exc = EditedEntityFailedToPromote(list(resolved.landing_failures))
    elif "CCR_EXTRACTION_INVALID" in codes or any(
        code.startswith("CCR_") and "OPERATION" not in code and "REVIEW_EDIT" not in code
        for code in codes
    ):
        exc = IncoherentCcrExtraction(
            [issue.explanation for issue in preview.issues]
        )
    elif "CCR_OPERATION_VERSION_STALE" in codes:
        exc = StaleStructuralOperation(base_version=-1, current_version=preview.review_version)
    elif "CCR_OPERATION_INVALID" in codes:
        exc = InvalidStructuralOperation(
            "Stored structural operation is invalid.",
            [
                issue.category_key
                for issue in preview.issues
                if issue.category_key is not None
            ],
        )
    else:
        from app.dre_extraction.promotion import UnresolvableReviewEdit

        exc = UnresolvableReviewEdit([])
    setattr(exc, "promotion_issues", issue_dicts)
    raise exc


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
        "SELECT id, operator_unit_factors_json, parsed_json "
        "FROM dre_extraction_runs "
        "WHERE id = ? AND property_id = ?",
        (extraction_run_id, property_id),
    ).fetchone()
    if row is None:
        raise ExtractionRunNotFound(
            f"extraction_run_id={extraction_run_id} not found for property_id={property_id}"
        )

    extraction = parse_extraction_payload(row[2])
    if extraction is not None:
        _validate_replacement_roster(
            extraction,
            [factor.unit_number for factor in factors],
        )

    existing_factors = {
        str(unit_number).strip(): entry
        for unit_number, entry in parse_operator_unit_factors(row[1]).items()
        if isinstance(entry, dict)
    }
    factors_dict: dict[str, dict] = {}
    for f in factors:
        unit_number = f.unit_number.strip()
        entry: dict[str, Any] = dict(existing_factors.get(unit_number, {}))
        if f.square_feet is not None:
            entry["square_feet"] = str(f.square_feet)
        if f.ownership_percent is not None:
            entry["ownership_percent"] = str(f.ownership_percent)
        if f.fixed_amounts:
            fixed_amounts = (
                dict(entry.get("fixed_amounts", {}))
                if isinstance(entry.get("fixed_amounts"), dict)
                else {}
            )
            fixed_amounts.update(
                {
                    pool_key: str(amount)
                    for pool_key, amount in f.fixed_amounts.items()
                }
            )
            entry["fixed_amounts"] = fixed_amounts
        if f.custom_factors:
            custom_factors = (
                dict(entry.get("custom_factors", {}))
                if isinstance(entry.get("custom_factors"), dict)
                else {}
            )
            custom_factors.update(
                {
                    pool_key: str(value)
                    for pool_key, value in f.custom_factors.items()
                }
            )
            entry["custom_factors"] = custom_factors
        factors_dict[unit_number] = entry

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
    if row is None:
        return {}
    return parse_operator_unit_factors(row[0])


def parse_operator_unit_factors(
    operator_factors_raw: Optional[str],
) -> dict[str, dict]:
    """Parse one captured factor snapshot without performing another read."""
    if not operator_factors_raw:
        return {}
    try:
        result = json.loads(operator_factors_raw)
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

    _validate_replacement_roster(extraction, list(operator_factors))

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
        fixed_amounts_raw = factor_entry.get("fixed_amounts")
        custom_factors_raw = factor_entry.get("custom_factors")

        def _dec(v: Any) -> Optional[Decimal]:
            if v is None:
                return None
            try:
                return Decimal(str(v))
            except InvalidOperation:
                return None

        sq_ft = _dec(sq_ft_raw)
        own_pct = _dec(own_pct_raw)
        fixed_amounts = (
            {
                str(pool_key): amount
                for pool_key, raw_amount in fixed_amounts_raw.items()
                if (amount := _dec(raw_amount)) is not None
            }
            if isinstance(fixed_amounts_raw, dict)
            else {}
        )
        custom_factors = (
            {
                str(pool_key): value
                for pool_key, raw_value in custom_factors_raw.items()
                if (value := _dec(raw_value)) is not None and value > 0
            }
            if isinstance(custom_factors_raw, dict)
            else {}
        )
        existing_pool_factors = list(existing.pool_factors) if existing is not None else []
        replaced_keys = set(fixed_amounts) | set(custom_factors)
        pool_factors = [
            factor
            for factor in existing_pool_factors
            if not (
                factor.pool_key in replaced_keys
            )
        ]
        pool_factors.extend(
            UnitPoolFactor(
                pool_key=pool_key,
                factor_value=amount,
                factor_label="Operator-entered annual amount",
                factor_type="dollar_amount",
                source_page=None,
            )
            for pool_key, amount in fixed_amounts.items()
        )
        pool_factors.extend(
            UnitPoolFactor(
                pool_key=pool_key,
                factor_value=value,
                factor_label="Operator-entered custom factor",
                factor_type="raw_factor",
                source_page=None,
            )
            for pool_key, value in custom_factors.items()
        )

        if existing is not None:
            merged_units.append(
                existing.model_copy(
                    update={
                        "square_feet": sq_ft if sq_ft is not None else existing.square_feet,
                        "ownership_percent": (
                            own_pct if own_pct is not None else existing.ownership_percent
                        ),
                        "pool_factors": pool_factors,
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
                    pool_factors=pool_factors,
                )
            )

    # Also include any existing units NOT covered by operator factors.
    # Compare against the normalized operator keys so a whitespace-only
    # difference doesn't re-add a unit that was already merged above (M6).
    known_count = extraction.unit_structure.unit_count
    if known_count is None or known_count <= 0:
        for unit_num, unit in existing_by_num.items():
            if unit_num not in normalized_operator_keys:
                merged_units.append(unit)

    unit_structure = extraction.unit_structure.model_copy(
        update={
            "units": merged_units,
            "unit_count": (
                known_count
                if known_count is not None and known_count > 0
                else len(merged_units)
            ),
        }
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

    resolved = resolve_ccr_promotion(
        property_id=property_id,
        extraction_run_id=extraction_run_id,
        setup_type=setup_type,
        connection=connection,
    )
    _raise_preview_blockers(resolved)
    _pin_ccr_promotion_inputs(
        property_id=property_id,
        extraction_run_id=extraction_run_id,
        resolved=resolved,
        connection=connection,
    )
    extraction = resolved.preview.resolved_extraction
    if extraction is None:  # narrowed by _raise_preview_blockers
        raise RuntimeError("resolved CC&R extraction unexpectedly missing")
    edited_entity_keys = resolved.edited_entity_keys

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
    """Re-promote the exact same resolved candidate returned by preview."""
    resolved = resolve_ccr_promotion(
        property_id=property_id,
        extraction_run_id=extraction_run_id,
        setup_type=setup_type,
        connection=connection,
    )
    _raise_preview_blockers(resolved)
    _pin_ccr_promotion_inputs(
        property_id=property_id,
        extraction_run_id=extraction_run_id,
        resolved=resolved,
        connection=connection,
    )
    extraction = resolved.preview.resolved_extraction
    if extraction is None:
        raise RuntimeError("resolved CC&R extraction unexpectedly missing")

    return reopen_and_repromote(
        property_id=property_id,
        extraction_run_id=extraction_run_id,
        setup_type=setup_type,
        reviewed_by=reviewed_by,
        connection=connection,
        resolved_extraction=extraction,
        resolved_edited_entity_keys=resolved.edited_entity_keys,
    )


__all__ = [
    "CCRUnitFactor",
    "IncompleteOperatorUnitRoster",
    "MissingUnitFactors",
    "IncoherentCcrExtraction",
    "save_operator_unit_factors",
    "get_operator_unit_factors",
    "parse_operator_unit_factors",
    "merge_operator_factors",
    "CCRPromotionIssue",
    "CCRPromotionPreview",
    "PromotionInputsChanged",
    "resolve_ccr_promotion",
    "approve_ccr_extraction_run",
    "reopen_and_repromote_ccr_run",
]
