"""Snapshot ``DREExtractionRun.parsed_json`` into the live setup tables (Task 105).

On approve, the approval service writes an ``assessment_setups`` row. This
module adds the child rows: AllocationPool, AssessmentGroup, AssessmentUnit,
and AssessmentUnitPoolAllocation. Without these, the engine has no recipients
or pools to allocate against — the AssessmentSetup row alone is just a
header.

The mapping is intentionally lossy on the AI-prompt vocabulary:

* ``setup_type`` is chosen by the operator at approval-time
  (``fixed`` | ``grouped`` | ``per_unit``) — not mapped from prompt enums here.
* ``allocation_method`` mapping uses ``adapter.map_allocation_method`` —
  e.g. ``parking_space`` collapses to ``equal`` over ``parking_users``
  scope. We honor ``forced_scope`` even when the prompt also emitted
  ``recipient_scope``, because the prompt's free-text scope can drift.
* Groups vs units: we populate AssessmentGroup rows when the extraction
  produced any group rows AND the chosen setup_type is ``grouped``. We
  populate AssessmentUnit rows when units are present AND setup_type is
  ``per_unit``. For ``fixed`` setups we populate neither (the engine
  fans out across ``properties.units`` at recipient resolution time).
* ``AssessmentUnitPoolAllocation`` is populated from per-pool
  ``annual_amount``/``monthly_amount`` divided across the matching unit
  rows when a ``specified_value`` pool exists — otherwise the engine's
  specified_value allocator can't find a per-unit value at runtime.

The snapshot is **best-effort**: any single bad row logs a warning and is
skipped rather than aborting the whole promotion. The operator's edits in
the Review Workbench (Phase 4, deferred) will eventually correct or
override before promotion, but we honor whatever shape the extraction
produced today so the engine has something to compute against.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional, Union, get_args, get_origin

from ..assessment_engine.percent_form import (
    AmbiguousPercentColumn,
    normalize_percent_value,
    resolve_percent_divisor,
)
from .adapter import map_allocation_method
from .schemas import (
    AllocationPoolBlock,
    DRESetupExtraction,
    GroupRow,
    UnitRow,
)


logger = logging.getLogger(__name__)


class UnresolvableReviewEdit(RuntimeError):
    """Raised when one or more ``dre_review_edits`` rows reference a
    ``field_path`` that cannot be resolved (or type-coerced) against the
    current parsed extraction — e.g. the pool at that index was removed by
    a prior edit, or the value can't be coerced to the field's declared
    type. The operator explicitly made this edit, so dropping or
    misapplying it silently would produce a wrong assessment with no
    visible trace; the endpoint maps this to HTTP 422 naming every
    unresolvable path so the operator can re-enter it.
    """

    def __init__(self, unresolvable_field_paths: list[str]) -> None:
        self.unresolvable_field_paths = unresolvable_field_paths
        super().__init__(
            f"Cannot promote: review edit(s) reference field_path(s) "
            f"{unresolvable_field_paths!r} that cannot be resolved against "
            "the current extraction. Re-enter the edit(s) against the "
            "current data before re-attempting promotion."
        )


_PROPORTIONAL_ALLOCATION_METHODS = frozenset(
    {"square_footage", "ownership_percentage", "custom_factor"}
)


class MissingUnitFactors(RuntimeError):
    """Raised when a ``per_unit`` setup has a proportional-allocation pool
    (square_footage / ownership_percentage / custom_factor) but no unit
    carries per-unit data at all.

    Originally a CC&R-only guard, since CC&Rs commonly reference a
    proportional basis without carrying machine-readable per-unit data —
    but a manual per_unit setup (and, in principle, a DRE one) has the
    identical risk: an operator declares ``square_footage`` allocation and
    forgets to enter square footage for any unit. Shared here so every
    promotion path that populates units enforces the same guard rather
    than silently producing an equal-distribution assessment that looks
    intentional but isn't.
    """

    def __init__(self, missing_pool_keys: list[str]) -> None:
        self.missing_pool_keys = missing_pool_keys
        super().__init__(
            f"Cannot promote: proportional pool(s) {missing_pool_keys!r} have no "
            "per-unit factors. Enter unit factors before promoting."
        )


def check_missing_unit_factors(extraction: DRESetupExtraction) -> list[str]:
    """Return ``pool_key``s of proportional-allocation pools that have no
    per-unit unit data at all (i.e. ``unit_structure.units`` is empty).
    """
    has_units = bool(extraction.unit_structure.units)
    missing: list[str] = []
    for pool in extraction.allocation_pools:
        if pool.allocation_method in _PROPORTIONAL_ALLOCATION_METHODS:
            if not has_units:
                missing.append(pool.pool_key)
    return missing


class AmbiguousOwnershipPercentForm(RuntimeError):
    """Raised at promotion when the ownership-percent column's sum resolves
    to neither fraction form (~1.0) nor points form (~100) AND a pool
    actually allocates by ownership percentage — so the ambiguity would
    poison homeowner-visible math.

    The operator resolves it with an audited review edit setting
    ``unit_structure.ownership_percent_form`` to ``'fraction'`` or
    ``'points'``, then re-approves. Mapped to HTTP 422 by the approval
    routers. Columns that are ambiguous but display-only (no
    ownership_percentage pool) do NOT raise — they store verbatim and the
    render-side column resolver guards the display.
    """

    def __init__(self, cause: AmbiguousPercentColumn) -> None:
        self.column_label = cause.column_label
        self.total = cause.total
        self.sample_values = cause.sample_values
        super().__init__(
            str(cause)
            + " Set unit_structure.ownership_percent_form to 'fraction' or "
            "'points' as a review edit, then re-approve."
        )


class EditedEntityFailedToPromote(RuntimeError):
    """Raised when an operator-edited pool/group/unit could not be inserted
    into the live setup at promotion time (e.g. an edited
    ``allocation_method`` that ``map_allocation_method`` can't map, or an
    edited ``unit_count``/``unit_number`` that is still invalid).

    This is distinct from the best-effort skip applied to AI-extracted rows
    that were never touched by an operator: a malformed *extraction* row is
    the model's mistake and safe to skip, but a field an *operator*
    explicitly edited must never silently vanish — that is exactly the
    "I edited it and got no error, but it had no effect" bug this
    capability exists to close.
    """

    def __init__(self, entity_refs: list[str]) -> None:
        self.entity_refs = entity_refs
        super().__init__(
            f"Cannot promote: operator-edited entities {entity_refs!r} could "
            "not be inserted into the live setup. Correct the edited "
            "value(s) in the Review Workbench and retry."
        )


_PATH_SEGMENT_RE = re.compile(r"^(\w+)(\[(\d+)\])?$")


def _parse_field_path(field_path: str) -> Optional[list[tuple[str, Optional[int]]]]:
    """Parse a dotted/bracketed ``field_path`` into ``(name, index)`` segments.

    E.g. ``allocation_pools[0].denominator_value`` ->
    ``[("allocation_pools", 0), ("denominator_value", None)]``. Returns
    ``None`` if the path doesn't match the expected grammar.
    """
    segments: list[tuple[str, Optional[int]]] = []
    for raw in field_path.split("."):
        match = _PATH_SEGMENT_RE.match(raw)
        if not match:
            return None
        name, _, idx = match.groups()
        segments.append((name, int(idx) if idx is not None else None))
    return segments or None


def _resolve_edit_target(
    root: DRESetupExtraction, segments: list[tuple[str, Optional[int]]]
) -> tuple[Any, str]:
    """Walk all but the last path segment and return ``(target_obj, attr_name)``
    for the leaf field the last segment names.

    Raises ``AttributeError``/``IndexError``/``TypeError`` on an
    unresolvable path — callers treat any of these as unresolvable.
    """
    obj: Any = root
    for name, idx in segments[:-1]:
        obj = getattr(obj, name)
        if idx is not None:
            obj = obj[idx]
    last_name, last_idx = segments[-1]
    if last_idx is not None:
        # The path names a whole list element as the target, not a field —
        # unsupported by this resolver (every editable field today is a
        # leaf scalar on a pool/group/unit/setup object).
        raise TypeError(f"path segment {last_name}[{last_idx}] is not a leaf field")
    return obj, last_name


def _unwrap_optional(annotation: Any) -> Any:
    if get_origin(annotation) is Union:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _coerce_edit_value(annotation: Any, raw_new_value: Optional[str]) -> Any:
    """Coerce a ``dre_review_edits.new_value`` TEXT column back to the
    target field's declared type.

    ``new_value`` is stored as TEXT (``_stringify_value`` in
    ``dre_review_service.py``); applying that raw string into a typed
    ``Decimal``/``bool``/``int`` field is itself a silent-corruption path,
    not just a type-checker nuisance — same ``Decimal(str(x))`` pattern
    ``wire_to_domain.py`` uses for Gemini output.
    """
    if raw_new_value is None:
        return None
    target_type = _unwrap_optional(annotation)
    if target_type is Decimal:
        return Decimal(raw_new_value)
    if target_type is bool:
        normalized = raw_new_value.strip().lower()
        if normalized in ("true", "1", "yes"):
            return True
        if normalized in ("false", "0", "no"):
            return False
        raise ValueError(f"cannot coerce {raw_new_value!r} to bool")
    if target_type is int:
        return int(raw_new_value)
    if target_type is float:
        return float(raw_new_value)
    return raw_new_value


def _entity_ref_for_segments(
    extraction: DRESetupExtraction, segments: list[tuple[str, Optional[int]]]
) -> Optional[str]:
    """Return a ``"pool:<pool_key>"`` / ``"group:<key>"`` / ``"unit:<unit_number>"``
    reference for a path's first segment(s), or ``None`` for paths that
    don't address a pool/group/unit (e.g. top-level ``assessment_setup.*``).
    """
    if not segments:
        return None
    first_name, first_idx = segments[0]
    if first_name == "allocation_pools" and first_idx is not None:
        try:
            pool = extraction.allocation_pools[first_idx]
        except IndexError:
            return None
        return f"pool:{pool.pool_key}"
    if first_name == "unit_structure" and len(segments) > 1:
        second_name, second_idx = segments[1]
        if second_name == "groups" and second_idx is not None:
            try:
                group = extraction.unit_structure.groups[second_idx]
            except IndexError:
                return None
            key = group.group_id or group.label or str(second_idx)
            return f"group:{key}"
        if second_name == "units" and second_idx is not None:
            try:
                unit = extraction.unit_structure.units[second_idx]
            except IndexError:
                return None
            return f"unit:{unit.unit_number}"
    return None


def apply_review_edits_to_extraction(
    extraction: DRESetupExtraction,
    edits: Iterable[Any],
) -> DRESetupExtraction:
    """Patch a parsed extraction with the latest Review Workbench edit per
    ``field_path`` and return the patched extraction.

    ``edits`` is any iterable of objects exposing ``field_path`` and
    ``new_value`` (the shape ``dre_review_service.list_review_edits``
    returns), ordered oldest-first — the latest edit per path wins, since
    ``dre_review_edits`` is append-only.

    Raises ``UnresolvableReviewEdit`` naming every ``field_path`` that
    can't be resolved against the extraction tree or coerced to its
    field's declared type — never silently dropped or misapplied.
    """
    latest_by_path: dict[str, Any] = {}
    for edit in edits:
        latest_by_path[edit.field_path] = edit
    if not latest_by_path:
        return extraction

    working = extraction.model_copy(deep=True)
    unresolvable: list[str] = []

    for field_path, edit in latest_by_path.items():
        segments = _parse_field_path(field_path)
        if segments is None:
            unresolvable.append(field_path)
            continue
        try:
            target_obj, attr_name = _resolve_edit_target(working, segments)
            model_fields = type(target_obj).model_fields
            if attr_name not in model_fields:
                raise AttributeError(attr_name)
            annotation = model_fields[attr_name].annotation
            coerced = _coerce_edit_value(annotation, edit.new_value)
            setattr(target_obj, attr_name, coerced)
        except (
            AttributeError,
            IndexError,
            TypeError,
            ValueError,
            InvalidOperation,
            KeyError,
        ):
            unresolvable.append(field_path)
            continue

    if unresolvable:
        raise UnresolvableReviewEdit(unresolvable)

    return working


def entity_keys_touched_by_edits(
    extraction: DRESetupExtraction, field_paths: Iterable[str]
) -> frozenset[str]:
    """Return the set of ``"pool:<key>"``/``"group:<key>"``/``"unit:<key>"``
    refs that the given (already-applied) edit ``field_paths`` touched.

    Called after :func:`apply_review_edits_to_extraction` succeeds, against
    the *patched* extraction, so ``populate_setup_children`` can tell an
    edited pool/group/unit apart from one the AI extracted with bad data —
    the former must raise instead of silently skipping (see
    ``EditedEntityFailedToPromote``).
    """
    keys: set[str] = set()
    for field_path in field_paths:
        segments = _parse_field_path(field_path)
        if segments is None:
            continue
        ref = _entity_ref_for_segments(extraction, segments)
        if ref is not None:
            keys.add(ref)
    return frozenset(keys)


_VALID_RECIPIENT_SCOPES = {
    "all_units", "residential_only", "commercial_only",
    "parking_users", "custom_unit_list",
}
_VALID_DENOMINATOR_SOURCES = {"dre_value", "calculated", "manual"}


def _coerce_recipient_scope(raw: str) -> str:
    """Normalize prompt-emitted ``recipient_scope`` to internal enum.

    Defaults to ``all_units`` for empty/unrecognized values — the engine
    fans out across every unit then, which is the safest default.
    """
    candidate = (raw or "").strip().lower().replace(" ", "_")
    if candidate in _VALID_RECIPIENT_SCOPES:
        return candidate
    return "all_units"


def _coerce_denominator_source(raw: str) -> str:
    candidate = (raw or "").strip().lower()
    # Prompt vocab: 'dre_shown' | 'calculated' | 'unknown'
    if candidate == "dre_shown":
        return "dre_value"
    if candidate == "calculated":
        return "calculated"
    if candidate in _VALID_DENOMINATOR_SOURCES:
        return candidate
    return "dre_value"


def _insert_pool(
    *,
    setup_id: int,
    pool: AllocationPoolBlock,
    display_order: int,
    connection: sqlite3.Connection,
) -> Optional[int]:
    """Insert one allocation_pools row. Returns pool_id, or None on bad data."""
    mapping = map_allocation_method(pool.allocation_method)
    if mapping.internal_method is None:
        logger.warning(
            "promotion: skipping pool %r — allocation method %r could not be mapped",
            pool.pool_key, pool.allocation_method,
        )
        return None

    scope = mapping.forced_scope or _coerce_recipient_scope(pool.recipient_scope)
    denom_source = (
        mapping.forced_denominator_source
        or _coerce_denominator_source(pool.denominator_source)
    )

    cur = connection.execute(
        """
        INSERT INTO allocation_pools (
            assessment_setup_id, pool_key, pool_name, denominator_label,
            allocation_method, recipient_scope,
            denominator_source, denominator_value,
            variable_flag, display_order, include_in_pdf,
            budget_line_derivation,
            residual_after_pool_keys_json,
            residual_exclusions_json,
            pool_kind
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
        """,
        (
            setup_id, pool.pool_key, pool.pool_name or pool.pool_key,
            pool.denominator_label or None,
            mapping.internal_method, scope,
            denom_source,
            str(pool.denominator_value) if pool.denominator_value is not None else None,
            1 if pool.variable_flag else 0,
            display_order,
            pool.budget_line_derivation,
            json.dumps(pool.residual_after_pool_keys),
            json.dumps(pool.residual_exclusions),
            pool.pool_kind or None,
        ),
    )
    return cur.lastrowid


def _insert_group(
    *,
    setup_id: int,
    group: GroupRow,
    display_order: int,
    connection: sqlite3.Connection,
    percent_divisor: Optional[Decimal] = None,
) -> Optional[int]:
    """Insert one assessment_groups row. Returns row id, or None on bad data.

    ``percent_divisor`` (C8) is the column-level resolution from
    ``resolve_percent_divisor`` — the stored ``ownership_percent`` is always
    the normalized FRACTION (printed value ÷ divisor).
    """
    if group.unit_count is None or group.unit_count <= 0:
        logger.warning(
            "promotion: skipping group %r — unit_count missing/invalid",
            group.group_id or group.label,
        )
        return None
    ownership = normalize_percent_value(group.ownership_percent, percent_divisor)
    cur = connection.execute(
        """
        INSERT INTO assessment_groups (
            assessment_setup_id, group_name, unit_count,
            average_square_feet, ownership_percent, dre_factor, display_order
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            setup_id,
            group.label or group.group_id or f"group-{display_order}",
            int(group.unit_count),
            str(group.average_square_feet) if group.average_square_feet is not None else None,
            str(ownership) if ownership is not None else None,
            str(group.factor) if group.factor is not None else None,
            display_order,
        ),
    )
    return cur.lastrowid


_CATEGORY_MAP = {
    "residential": "residential",
    "commercial": "commercial",
    "mixed": "mixed",
    "mixed_use": "mixed",
    "": None,
}


def _coerce_category(raw_category: str, residential_commercial_flag: str) -> Optional[str]:
    """Map prompt-emitted category to schema enum (residential|commercial|mixed)."""
    candidate = (raw_category or "").strip().lower().replace(" ", "_")
    if candidate in _CATEGORY_MAP:
        mapped = _CATEGORY_MAP[candidate]
        if mapped:
            return mapped
    flag = (residential_commercial_flag or "").strip().lower()
    if flag.startswith("res"):
        return "residential"
    if flag.startswith("com"):
        return "commercial"
    return None


def _parking_count(raw: str) -> int:
    """Parse ``parking_flag`` text → integer space count. Defaults to 0."""
    if not raw:
        return 0
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) if digits else 0


def _insert_unit(
    *,
    setup_id: int,
    unit: UnitRow,
    connection: sqlite3.Connection,
    percent_divisor: Optional[Decimal] = None,
) -> Optional[int]:
    """Insert one assessment_units row.

    ``percent_divisor`` (C8) is the column-level resolution from
    ``resolve_percent_divisor`` — the stored ``ownership_percent`` is always
    the normalized FRACTION (printed value ÷ divisor). The verbatim printed
    value stays in the immutable ``parsed_json`` payload for audit.
    """
    if not unit.unit_number:
        logger.warning("promotion: skipping unit — unit_number empty")
        return None
    ownership = normalize_percent_value(unit.ownership_percent, percent_divisor)
    cur = connection.execute(
        """
        INSERT INTO assessment_units (
            assessment_setup_id, unit_number, square_feet,
            ownership_percent, category, parking_spaces,
            specified_monthly_amount, source
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'dre')
        """,
        (
            setup_id, unit.unit_number,
            str(unit.square_feet) if unit.square_feet is not None else None,
            str(ownership) if ownership is not None else None,
            _coerce_category(unit.category, unit.residential_commercial_flag),
            _parking_count(unit.parking_flag),
        ),
    )
    return cur.lastrowid


# Source value for auto-generated equal splits on specified_value pools.
# NEVER 'dre': a 'dre' source on a unit-pool allocation must always mean the
# value was extracted from the document. Preflight blocks package generation
# and finalize while any specified_value pool still carries placeholder rows.
EQUAL_SPLIT_PLACEHOLDER_SOURCE = "equal_split_placeholder"

# Sum-test tolerance for disambiguating extracted per-unit dollar factors
# (monthly form vs annual form) against the pool's stated totals.
_DOLLAR_FACTOR_SUM_TOLERANCE = Decimal("0.005")  # 0.5%


def _within_tolerance(total: Decimal, target: Optional[Decimal]) -> bool:
    if target is None or target == 0:
        return False
    return abs(total - target) / abs(target) <= _DOLLAR_FACTOR_SUM_TOLERANCE


def _dollar_factors_for_pool(
    units: list[UnitRow],
    pool_key: str,
) -> dict[str, Decimal]:
    """Per-unit ``factor_type='dollar_amount'`` values for one pool.

    Returns ``{unit_number: value}`` including only units that carry exactly
    one non-null dollar factor for the pool.
    """
    out: dict[str, Decimal] = {}
    for unit in units:
        matches = [
            f.factor_value
            for f in unit.pool_factors
            if f.pool_key == pool_key
            and f.factor_type == "dollar_amount"
            and f.factor_value is not None
        ]
        if len(matches) == 1:
            out[unit.unit_number] = matches[0]
    return out


def _insert_specified_value_allocations(
    *,
    setup_id: int,
    pool_key: str,
    pool_id: int,
    unit_id_by_number: dict[str, int],
    annual_amount: Optional[Decimal],
    monthly_amount: Optional[Decimal],
    units: list[UnitRow],
    unit_count: int,
    connection: sqlite3.Connection,
    edited_entity_keys: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Insert one assessment_unit_pool_allocations row per unit (C7).

    Extraction captures real per-unit specified values as
    ``pool_factors`` entries with ``factor_type='dollar_amount'``. When
    every inserted unit carries one and the sum passes the 0.5% test
    against a pool total, those values are promoted verbatim with
    ``source='dre'``:

    - sum ≈ pool ``monthly_amount``  → values are monthly, used directly
    - sum×12 ≈ pool ``annual_amount`` → same monthly-form evidence
    - sum ≈ pool ``annual_amount``   → values are annual, divided by 12

    Anything else — no factors, partial coverage, or a failed/contradictory
    sum test — falls back to an equal split tagged
    ``source='equal_split_placeholder'`` (never ``'dre'``), which blocks
    package generation and finalize until the operator enters real values
    in the Review Workbench. Extracted and synthetic values are never mixed
    within one pool.

    Returns an audit dict: ``{"mode": "dre_dollar_factors"|"placeholder",
    "form": "monthly"|"annual"|None, "reason": str|None}``.
    """
    if not unit_id_by_number:
        return {"mode": "skipped", "form": None, "reason": "no units inserted"}

    factors = _dollar_factors_for_pool(units, pool_key)
    covered = set(factors) >= set(unit_id_by_number)

    form: Optional[str] = None
    reason: Optional[str] = None
    if covered:
        total = sum(factors[n] for n in unit_id_by_number)
        monthly_match = _within_tolerance(total, monthly_amount) or (
            _within_tolerance(total * Decimal(12), annual_amount)
        )
        annual_match = _within_tolerance(total, annual_amount)
        if monthly_match and not annual_match:
            form = "monthly"
        elif annual_match and not monthly_match:
            form = "annual"
        else:
            reason = (
                f"per-unit dollar factors sum to {total} which matches "
                f"{'both' if (monthly_match and annual_match) else 'neither'} "
                f"pool total (monthly={monthly_amount}, annual={annual_amount})"
            )
    elif factors:
        reason = (
            f"only {len(factors)}/{len(unit_id_by_number)} units carry a "
            "dollar_amount factor for this pool"
        )
    else:
        reason = "extraction carried no per-unit dollar_amount factors"

    if form is not None:
        for unit_number, unit_id in unit_id_by_number.items():
            value = factors[unit_number]
            monthly = (
                value if form == "monthly" else value / Decimal(12)
            ).quantize(Decimal("0.01"))
            # Provenance: values flowing from an operator-edited unit are
            # operator data (the workbench edit path applies edits to the
            # extraction's pool_factors, then re-promotes through here).
            # 'manual' is this schema's existing vocabulary for
            # operator-entered unit-pool values.
            source = (
                "manual"
                if f"unit:{unit_number}" in edited_entity_keys
                else "dre"
            )
            connection.execute(
                """
                INSERT INTO assessment_unit_pool_allocations (
                    assessment_unit_id, assessment_setup_id,
                    pool_key, pool_id, specified_monthly_amount, source
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (unit_id, setup_id, pool_key, pool_id, str(monthly), source),
            )
        return {"mode": "dre_dollar_factors", "form": form, "reason": None}

    # Placeholder fallback: equal split, explicitly tagged. Blocked by
    # preflight until the operator resolves it — never a guess shipped
    # as document data.
    if annual_amount is None or unit_count <= 0:
        return {"mode": "skipped", "form": None, "reason": reason}
    logger.warning(
        "promotion: specified_value pool %r fell back to equal-split "
        "placeholder (%s); operator must enter per-unit values before "
        "package generation",
        pool_key,
        reason,
    )
    monthly = (annual_amount / Decimal(12) / Decimal(unit_count)).quantize(
        Decimal("0.01")
    )
    for unit_number, unit_id in unit_id_by_number.items():
        connection.execute(
            """
            INSERT INTO assessment_unit_pool_allocations (
                assessment_unit_id, assessment_setup_id,
                pool_key, pool_id, specified_monthly_amount, source
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (unit_id, setup_id, pool_key, pool_id, str(monthly),
             EQUAL_SPLIT_PLACEHOLDER_SOURCE),
        )
    return {"mode": "placeholder", "form": None, "reason": reason}


def parse_extraction_payload(
    parsed_json_text: Optional[str],
) -> Optional[DRESetupExtraction]:
    """Parse a stored ``dre_extraction_runs.parsed_json`` blob.

    Returns None when the blob is missing or fails validation — the
    caller should skip child-row population and let the operator fix
    the extraction in the Review Workbench.
    """
    if not parsed_json_text:
        return None
    try:
        payload = json.loads(parsed_json_text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("promotion: parsed_json is not valid JSON; skipping snapshot")
        return None
    try:
        return DRESetupExtraction.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError + edge cases
        logger.warning("promotion: parsed_json failed schema validation: %s", exc)
        return None


_PROPORTIONAL_SQFT_METHODS = frozenset({"square_footage", "custom_factor"})


def _is_parking_or_cost_center_pool(pool: AllocationPoolBlock) -> bool:
    """Pools that must not inherit whole-HOA sqft as a fake denominator."""
    blob = " ".join(
        [
            str(pool.pool_key or ""),
            str(pool.pool_name or ""),
            str(pool.denominator_label or ""),
        ]
    ).lower()
    markers = (
        "parking",
        "cost_center",
        "cost center",
        "limited common",
        "limited_common",
    )
    return any(m in blob for m in markers)


def _complete_recipient_sqft_denominator(
    extraction: DRESetupExtraction,
) -> Optional[Decimal]:
    """Sum of unit sqft, or group avg_sqft × unit_count, when coverage is complete.

    Returns None when any recipient is missing sqft (do not invent partial
    denominators). Matches engine recompute grain for unit and group scopes.
    """
    units = extraction.unit_structure.units
    groups = extraction.unit_structure.groups
    if units:
        if any(u.square_feet is None for u in units):
            return None
        total = sum((u.square_feet for u in units), start=Decimal("0"))
        return total if total > 0 else None
    if groups:
        total = Decimal("0")
        for g in groups:
            if g.average_square_feet is None or g.unit_count is None or g.unit_count <= 0:
                return None
            total += g.average_square_feet * Decimal(g.unit_count)
        return total if total > 0 else None
    return None


def _normalize_proportional_pool_methods(
    extraction: DRESetupExtraction,
) -> DRESetupExtraction:
    """Reconcile a declared ``square_footage`` / ``custom_factor`` basis with unit data.

    A governing document may state that costs are split "in proportion to
    square footage" while the only machine-readable per-unit factor it carries
    is a *percentage interest* (e.g. an Exhibit B that lists each unit's % of
    the whole). A percentage interest is the normalized square-footage share,
    so the document's intent is preserved by allocating via
    ``ownership_percentage`` — which the engine can actually compute. Without
    this, a ``square_footage`` pool with no per-unit square feet and no
    denominator makes the engine raise ``UnsupportedAllocationMethod`` and the
    whole package render fails.

    Conservative by design: only rewrites when NO recipient (unit or group)
    carries square footage AND at least one carries a percentage. A setup with
    genuine per-unit square footage is left untouched (denominators may still
    be filled by :func:`_fill_missing_square_footage_denominators`).
    """
    units = extraction.unit_structure.units
    groups = extraction.unit_structure.groups

    any_square_feet = any(u.square_feet is not None for u in units) or any(
        g.average_square_feet is not None for g in groups
    )
    any_percent = any(u.ownership_percent is not None for u in units) or any(
        g.ownership_percent is not None for g in groups
    )
    if any_square_feet or not any_percent:
        return extraction

    new_pools: list[AllocationPoolBlock] = []
    changed = False
    for pool in extraction.allocation_pools:
        if pool.allocation_method in _PROPORTIONAL_SQFT_METHODS:
            if _is_parking_or_cost_center_pool(pool):
                new_pools.append(pool)
                continue
            logger.info(
                "promotion: pool %r declared %s but no recipient has "
                "square feet; allocating by ownership_percentage (percentage "
                "interest is the normalized square-footage share)",
                pool.pool_key,
                pool.allocation_method,
            )
            new_pools.append(
                pool.model_copy(update={"allocation_method": "ownership_percentage"})
            )
            changed = True
        else:
            new_pools.append(pool)

    if not changed:
        return extraction
    return extraction.model_copy(update={"allocation_pools": new_pools})


def _fill_missing_square_footage_denominators(
    extraction: DRESetupExtraction,
) -> DRESetupExtraction:
    """Set denominator_value from complete unit/group sqft when method needs it.

    Safe for equal / ownership / hybrid residual pools (untouched). Skips
    parking and cost-center pools (would invent the wrong basis). Only fills
    when every recipient has sqft so partial tables never become silent truth.
    """
    sqft_total = _complete_recipient_sqft_denominator(extraction)
    if sqft_total is None:
        return extraction

    new_pools: list[AllocationPoolBlock] = []
    changed = False
    for pool in extraction.allocation_pools:
        if pool.denominator_value is not None:
            new_pools.append(pool)
            continue
        if pool.allocation_method not in _PROPORTIONAL_SQFT_METHODS:
            new_pools.append(pool)
            continue
        if _is_parking_or_cost_center_pool(pool):
            new_pools.append(pool)
            continue
        logger.info(
            "promotion: pool %r missing denominator_value; "
            "using complete recipient sqft total %s (source=calculated)",
            pool.pool_key,
            sqft_total,
        )
        new_pools.append(
            pool.model_copy(
                update={
                    "denominator_value": sqft_total,
                    "denominator_source": "calculated",
                }
            )
        )
        changed = True

    if not changed:
        return extraction
    return extraction.model_copy(update={"allocation_pools": new_pools})


def populate_setup_children(
    *,
    setup_id: int,
    setup_type: str,
    extraction: DRESetupExtraction,
    connection: sqlite3.Connection,
    edited_entity_keys: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Insert AllocationPool / Group / Unit / UnitPoolAllocation rows.

    ``edited_entity_keys`` (from :func:`entity_keys_touched_by_edits`) names
    the pools/groups/units an operator edited. A row that fails to insert
    is normally a best-effort skip (the AI extracted something unusable),
    but if its key is in ``edited_entity_keys`` that means an operator's
    edit couldn't land — raises ``EditedEntityFailedToPromote`` instead of
    silently dropping it.

    Returns a count summary for the audit trail.
    """
    extraction = _normalize_proportional_pool_methods(extraction)
    extraction = _fill_missing_square_footage_denominators(extraction)

    counts = {
        "pools": 0, "groups": 0, "units": 0, "unit_pool_allocations": 0,
    }
    failed_edited_entities: list[str] = []

    # C8: resolve the ownership-percent column form ONCE, column-level,
    # before any row insert — the stored value is always the normalized
    # fraction. The operator's audited review decision
    # (unit_structure.ownership_percent_form) short-circuits the sum test.
    # An ambiguous column blocks promotion only when a pool actually
    # allocates by ownership percentage; display-only columns store
    # verbatim and the render-side resolver guards them.
    has_ownership_pool = any(
        pool.allocation_method == "ownership_percentage"
        for pool in extraction.allocation_pools
    )
    forced_form = extraction.unit_structure.ownership_percent_form

    def _percent_divisor_for(rows, label: str) -> Optional[Decimal]:
        try:
            return resolve_percent_divisor(
                [row.ownership_percent for row in rows],
                column_label=label,
                forced_form=forced_form,
            )
        except AmbiguousPercentColumn as exc:
            if has_ownership_pool:
                raise AmbiguousOwnershipPercentForm(exc) from exc
            logger.warning(
                "promotion: %s is ambiguous (%s) but no ownership_percentage "
                "pool exists; storing verbatim for the read-side resolver",
                label,
                exc,
            )
            return None

    group_percent_divisor = _percent_divisor_for(
        extraction.unit_structure.groups, "assessment_groups.ownership_percent"
    )
    unit_percent_divisor = _percent_divisor_for(
        extraction.unit_structure.units, "assessment_units.ownership_percent"
    )

    pool_id_by_key: dict[str, int] = {}
    for idx, pool in enumerate(extraction.allocation_pools):
        pool_id = _insert_pool(
            setup_id=setup_id, pool=pool, display_order=idx, connection=connection,
        )
        if pool_id is not None:
            pool_id_by_key[pool.pool_key] = pool_id
            counts["pools"] += 1
        elif f"pool:{pool.pool_key}" in edited_entity_keys:
            failed_edited_entities.append(f"pool:{pool.pool_key}")

    if setup_type == "grouped":
        for idx, group in enumerate(extraction.unit_structure.groups):
            group_key = group.group_id or group.label or str(idx)
            if _insert_group(
                setup_id=setup_id, group=group,
                display_order=idx, connection=connection,
                percent_divisor=group_percent_divisor,
            ) is not None:
                counts["groups"] += 1
            elif f"group:{group_key}" in edited_entity_keys:
                failed_edited_entities.append(f"group:{group_key}")

    unit_id_by_number: dict[str, int] = {}
    if setup_type == "per_unit":
        for unit in extraction.unit_structure.units:
            unit_id = _insert_unit(
                setup_id=setup_id, unit=unit, connection=connection,
                percent_divisor=unit_percent_divisor,
            )
            if unit_id is not None:
                unit_id_by_number[unit.unit_number] = unit_id
                counts["units"] += 1
            elif f"unit:{unit.unit_number}" in edited_entity_keys:
                failed_edited_entities.append(f"unit:{unit.unit_number}")

        # For each specified_value pool (C7): promote the extraction's
        # per-unit dollar_amount factors when they pass the sum test;
        # otherwise fall back to an equal split explicitly tagged
        # 'equal_split_placeholder' so preflight blocks until the operator
        # resolves it. Never an equal split masquerading as 'dre' data.
        for pool in extraction.allocation_pools:
            if pool.allocation_method != "specified_value":
                continue
            pool_id = pool_id_by_key.get(pool.pool_key)
            if pool_id is None:
                continue
            before = sum(1 for _ in unit_id_by_number)
            outcome = _insert_specified_value_allocations(
                setup_id=setup_id, pool_key=pool.pool_key,
                pool_id=pool_id, unit_id_by_number=unit_id_by_number,
                annual_amount=pool.annual_amount,
                monthly_amount=pool.monthly_amount,
                units=extraction.unit_structure.units,
                unit_count=len(unit_id_by_number),
                connection=connection,
                edited_entity_keys=edited_entity_keys,
            )
            if outcome["mode"] != "skipped":
                counts["unit_pool_allocations"] += before
            if outcome["mode"] == "placeholder":
                counts.setdefault("specified_value_placeholders", []).append(
                    {"pool_key": pool.pool_key, "reason": outcome["reason"]}
                )

    if failed_edited_entities:
        raise EditedEntityFailedToPromote(failed_edited_entities)

    return counts


__all__ = [
    "EditedEntityFailedToPromote",
    "MissingUnitFactors",
    "UnresolvableReviewEdit",
    "apply_review_edits_to_extraction",
    "check_missing_unit_factors",
    "entity_keys_touched_by_edits",
    "parse_extraction_payload",
    "populate_setup_children",
]
