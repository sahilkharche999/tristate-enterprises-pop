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
from typing import Any, Iterable, Literal, Optional, Union, get_args, get_origin

from pydantic import BaseModel, Field, TypeAdapter

from ..assessment_engine.percent_form import (
    AmbiguousPercentColumn,
    normalize_percent_value,
    resolve_percent_divisor,
)
from .adapter import map_allocation_method
from ..allocation_resolution.service import seed_resolution_from_promotion
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


STRUCTURAL_OPERATION_FIELD_PATH = "allocation_pools.$operation"


class StaleStructuralOperation(RuntimeError):
    """A pool operation was authored against an older operation version."""

    def __init__(self, *, base_version: int, current_version: int) -> None:
        self.base_version = base_version
        self.current_version = current_version
        super().__init__(
            f"Structural operation base version {base_version} is stale; "
            f"current version is {current_version}."
        )


class InvalidStructuralOperation(RuntimeError):
    """A typed pool operation cannot be safely replayed."""

    def __init__(self, message: str, category_keys: list[str]) -> None:
        self.category_keys = category_keys
        super().__init__(message)


class _PoolOperationBase(BaseModel):
    base_version: int = Field(ge=0)


class AddPoolOperation(_PoolOperationBase):
    operation: Literal["add"]
    category_key: str
    pool: AllocationPoolBlock


class SplitPoolOperation(_PoolOperationBase):
    operation: Literal["split"]
    category_key: str
    pools: list[AllocationPoolBlock] = Field(min_length=2)


class MergePoolOperation(_PoolOperationBase):
    operation: Literal["merge"]
    category_keys: list[str] = Field(min_length=2)
    pool: AllocationPoolBlock


class RemovePoolOperation(_PoolOperationBase):
    operation: Literal["remove"]
    category_key: str


class UpdatePoolOperation(_PoolOperationBase):
    operation: Literal["update"]
    category_key: str
    changes: dict[str, Any]


PoolStructuralOperation = Union[
    AddPoolOperation,
    SplitPoolOperation,
    MergePoolOperation,
    RemovePoolOperation,
    UpdatePoolOperation,
]
_POOL_OPERATION_ADAPTER = TypeAdapter(
    Union[
        AddPoolOperation,
        SplitPoolOperation,
        MergePoolOperation,
        RemovePoolOperation,
        UpdatePoolOperation,
    ]
)


def parse_pool_structural_operation(value: Any) -> PoolStructuralOperation:
    """Validate a JSON/dict pool operation into its typed representation."""
    if isinstance(value, str):
        value = json.loads(value)
    return _POOL_OPERATION_ADAPTER.validate_python(value)


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
    """Return proportional pools missing a positive factor for a participant."""
    def _is_positive_numeric(value: Any) -> bool:
        try:
            numeric = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return False
        return numeric.is_finite() and numeric > 0

    units = list(extraction.unit_structure.units)
    units_by_number = {
        str(unit.unit_number).strip(): unit
        for unit in units
        if str(unit.unit_number).strip()
    }
    missing: list[str] = []
    for pool in extraction.allocation_pools:
        if pool.allocation_method not in _PROPORTIONAL_ALLOCATION_METHODS:
            continue
        participant_numbers = (
            list(units_by_number)
            if pool.recipient_scope == "all_units"
            else [
                str(value).strip()
                for value in pool.selected_unit_numbers
                if str(value).strip()
            ]
        )
        if pool.recipient_scope != "all_units" and not participant_numbers:
            try:
                participant_numbers = _resolved_selected_unit_numbers(pool, units)
            except InvalidStructuralOperation:
                participant_numbers = []
        if not participant_numbers:
            missing.append(pool.pool_key)
            continue

        def _has_positive_factor(unit_number: str) -> bool:
            unit = units_by_number.get(unit_number)
            if unit is None:
                return False
            if pool.allocation_method == "square_footage":
                return unit.square_feet is not None and unit.square_feet > 0
            if pool.allocation_method == "ownership_percentage":
                return (
                    unit.ownership_percent is not None
                    and unit.ownership_percent > 0
                )
            return any(
                factor.pool_key == pool.pool_key
                and _is_positive_numeric(factor.factor_value)
                for factor in unit.pool_factors
            )

        if any(
            not _has_positive_factor(unit_number)
            for unit_number in participant_numbers
        ):
            missing.append(pool.pool_key)
    return missing


def validate_specified_value_pools(
    extraction: DRESetupExtraction,
) -> dict[str, "SpecifiedValueFactorValidation"]:
    """Validate every specified-value pool with the promotion validator."""
    units = list(extraction.unit_structure.units)
    units_by_number = {
        str(unit.unit_number).strip(): unit
        for unit in units
        if str(unit.unit_number).strip()
    }
    validations: dict[str, SpecifiedValueFactorValidation] = {}
    for pool in extraction.allocation_pools:
        if pool.allocation_method != "specified_value":
            continue
        participant_numbers = (
            list(units_by_number)
            if pool.recipient_scope == "all_units"
            else [
                str(value).strip()
                for value in pool.selected_unit_numbers
                if str(value).strip()
            ]
        )
        if pool.recipient_scope != "all_units" and not participant_numbers:
            try:
                participant_numbers = _resolved_selected_unit_numbers(pool, units)
            except InvalidStructuralOperation:
                participant_numbers = []
        validations[pool.pool_key] = validate_specified_value_factors(
            factors=_dollar_factors_for_pool(units, pool.pool_key),
            participant_numbers=participant_numbers,
            annual_amount=pool.annual_amount,
            monthly_amount=pool.monthly_amount,
        )
    return validations


def check_missing_specified_values(extraction: DRESetupExtraction) -> list[str]:
    """Return specified-value pools missing positive participant amounts."""
    return [
        pool_key
        for pool_key, validation in validate_specified_value_pools(extraction).items()
        if not validation.valid and validation.failure_kind == "missing"
    ]


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
    if get_origin(target_type) is list:
        parsed = json.loads(raw_new_value)
        return TypeAdapter(target_type).validate_python(parsed)
    return raw_new_value


def _pool_index_by_key(
    pools: list[AllocationPoolBlock], category_key: str
) -> int:
    matches = [index for index, pool in enumerate(pools) if pool.pool_key == category_key]
    if len(matches) != 1:
        raise InvalidStructuralOperation(
            f"Category key {category_key!r} must identify exactly one pool.",
            [category_key],
        )
    return matches[0]


def _validate_new_pool_keys(
    pools: list[AllocationPoolBlock],
    *,
    replacing_keys: set[str],
    new_pools: list[AllocationPoolBlock],
) -> None:
    new_keys = [pool.pool_key for pool in new_pools]
    if any(not key.strip() for key in new_keys) or len(new_keys) != len(set(new_keys)):
        raise InvalidStructuralOperation(
            "Added or replacement pools require unique, non-empty category keys.",
            new_keys,
        )
    existing = {
        pool.pool_key for pool in pools if pool.pool_key not in replacing_keys
    }
    collisions = sorted(existing.intersection(new_keys))
    if collisions:
        raise InvalidStructuralOperation(
            "Added or replacement category keys already exist.",
            collisions,
        )


def _validate_structural_pool(pool: AllocationPoolBlock) -> None:
    if pool.recipient_scope not in _VALID_RECIPIENT_SCOPES:
        raise InvalidStructuralOperation(
            "Category recipient scope is unsupported.",
            [pool.pool_key],
        )
    if pool.recipient_scope != "all_units":
        participants = [
            str(value).strip() for value in pool.selected_unit_numbers
        ]
        if (
            not participants
            or any(not value for value in participants)
            or len(set(participants)) != len(participants)
        ):
            raise InvalidStructuralOperation(
                "Selected-home categories require distinct participating homes.",
                [pool.pool_key],
            )
    if pool.amount_availability == "known" and (
        pool.annual_amount is None or pool.annual_amount <= 0
    ):
        raise InvalidStructuralOperation(
            "Known category amounts must be positive annual values.",
            [pool.pool_key],
        )


def _maintain_residual_pool_relationships(
    pools: list[AllocationPoolBlock],
) -> list[AllocationPoolBlock]:
    """Keep category dependencies valid after any structural operation.

    Residual categories are canonicalized to every peer with the same billing
    cadence. Other categories retain only references that still exist and never
    reference themselves. This makes add/split/merge/remove safe without asking
    an operator to edit the internal dependency graph.
    """
    existing_keys = {pool.pool_key for pool in pools}
    maintained: list[AllocationPoolBlock] = []
    for pool in pools:
        if pool.budget_line_derivation == "residual_default":
            relationships = [
                peer.pool_key
                for peer in pools
                if peer.pool_key != pool.pool_key
                and peer.billing_cadence == pool.billing_cadence
            ]
        else:
            relationships = [
                key
                for key in pool.residual_after_pool_keys
                if key in existing_keys and key != pool.pool_key
            ]
        maintained.append(
            pool.model_copy(update={"residual_after_pool_keys": relationships})
        )
    return maintained


def _apply_pool_structural_operation(
    pools: list[AllocationPoolBlock],
    operation: PoolStructuralOperation,
) -> list[AllocationPoolBlock]:
    result = list(pools)
    if isinstance(operation, AddPoolOperation):
        if operation.pool.pool_key != operation.category_key:
            raise InvalidStructuralOperation(
                "Add operation category_key must match pool.pool_key.",
                [operation.category_key, operation.pool.pool_key],
            )
        _validate_new_pool_keys(
            result, replacing_keys=set(), new_pools=[operation.pool]
        )
        _validate_structural_pool(operation.pool)
        result.append(operation.pool)
        return _maintain_residual_pool_relationships(result)

    if isinstance(operation, RemovePoolOperation):
        result.pop(_pool_index_by_key(result, operation.category_key))
        return _maintain_residual_pool_relationships(result)

    if isinstance(operation, UpdatePoolOperation):
        index = _pool_index_by_key(result, operation.category_key)
        if "pool_key" in operation.changes:
            raise InvalidStructuralOperation(
                "Update cannot rename a stable category key.",
                [operation.category_key],
            )
        unknown = set(operation.changes) - set(AllocationPoolBlock.model_fields)
        if unknown:
            raise InvalidStructuralOperation(
                "Update contains unsupported pool fields.",
                [operation.category_key],
            )
        updated_payload = result[index].model_dump(mode="python")
        updated_payload.update(operation.changes)
        if (
            "selected_unit_numbers" in operation.changes
            and "participant_unit_numbers" not in operation.changes
        ):
            updated_payload["participant_unit_numbers"] = operation.changes[
                "selected_unit_numbers"
            ]
        elif (
            "participant_unit_numbers" in operation.changes
            and "selected_unit_numbers" not in operation.changes
        ):
            updated_payload["selected_unit_numbers"] = operation.changes[
                "participant_unit_numbers"
            ]
        result[index] = AllocationPoolBlock.model_validate(updated_payload)
        _validate_structural_pool(result[index])
        return _maintain_residual_pool_relationships(result)

    if isinstance(operation, SplitPoolOperation):
        index = _pool_index_by_key(result, operation.category_key)
        _validate_new_pool_keys(
            result,
            replacing_keys={operation.category_key},
            new_pools=operation.pools,
        )
        for pool in operation.pools:
            _validate_structural_pool(pool)
        result[index : index + 1] = operation.pools
        return _maintain_residual_pool_relationships(result)

    indexes = [
        _pool_index_by_key(result, category_key)
        for category_key in operation.category_keys
    ]
    if len(indexes) != len(set(indexes)):
        raise InvalidStructuralOperation(
            "Merge category keys must be unique.",
            operation.category_keys,
        )
    _validate_new_pool_keys(
        result,
        replacing_keys=set(operation.category_keys),
        new_pools=[operation.pool],
    )
    _validate_structural_pool(operation.pool)
    insert_at = min(indexes)
    result = [
        pool
        for index, pool in enumerate(result)
        if index not in set(indexes)
    ]
    result.insert(insert_at, operation.pool)
    return _maintain_residual_pool_relationships(result)


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
    ordered_edits = list(edits)
    latest_scalar_index: dict[str, int] = {
        edit.field_path: index
        for index, edit in enumerate(ordered_edits)
        if edit.field_path != STRUCTURAL_OPERATION_FIELD_PATH
    }
    if not ordered_edits:
        return extraction

    working = extraction.model_copy(deep=True)
    unresolvable: list[str] = []
    structural_version = 0

    for index, edit in enumerate(ordered_edits):
        field_path = edit.field_path
        if field_path == STRUCTURAL_OPERATION_FIELD_PATH:
            try:
                operation = parse_pool_structural_operation(edit.new_value)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise InvalidStructuralOperation(
                    "Stored structural operation is invalid.", []
                ) from exc
            if operation.base_version != structural_version:
                raise StaleStructuralOperation(
                    base_version=operation.base_version,
                    current_version=structural_version,
                )
            working.allocation_pools = _apply_pool_structural_operation(
                list(working.allocation_pools), operation
            )
            structural_version += 1
            continue
        if latest_scalar_index[field_path] != index:
            continue
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
            if isinstance(target_obj, AllocationPoolBlock):
                if attr_name == "selected_unit_numbers":
                    target_obj.participant_unit_numbers = list(coerced)
                elif attr_name == "participant_unit_numbers":
                    target_obj.selected_unit_numbers = list(coerced)
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
    extraction: DRESetupExtraction, edits_or_field_paths: Iterable[Any]
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
    for edit_or_path in edits_or_field_paths:
        field_path = (
            edit_or_path
            if isinstance(edit_or_path, str)
            else edit_or_path.field_path
        )
        if field_path == STRUCTURAL_OPERATION_FIELD_PATH:
            if isinstance(edit_or_path, str):
                continue
            try:
                operation = parse_pool_structural_operation(edit_or_path.new_value)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if isinstance(operation, MergePoolOperation):
                pool_keys = [*operation.category_keys, operation.pool.pool_key]
            elif isinstance(operation, SplitPoolOperation):
                pool_keys = [
                    operation.category_key,
                    *(pool.pool_key for pool in operation.pools),
                ]
            elif isinstance(operation, AddPoolOperation):
                pool_keys = [operation.category_key]
            else:
                pool_keys = [operation.category_key]
            keys.update(f"pool:{pool_key}" for pool_key in pool_keys)
            continue
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

    Unsupported values fail closed. Silently broadening an unknown subset to
    every home would create owner charges that the reviewed document did not
    authorize.
    """
    candidate = (raw or "").strip().lower().replace(" ", "_")
    if candidate in _VALID_RECIPIENT_SCOPES:
        return candidate
    raise InvalidStructuralOperation(
        "Category recipient scope is unsupported.",
        [],
    )


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
    if mapping.internal_method is None and not mapping.promote_as_unresolved:
        logger.warning(
            "promotion: skipping pool %r — allocation method %r could not be mapped",
            pool.pool_key, pool.allocation_method,
        )
        return None

    written_method = (
        "unresolved" if mapping.promote_as_unresolved else mapping.internal_method
    )
    declared_scope = mapping.forced_scope or _coerce_recipient_scope(
        pool.recipient_scope
    )
    scope = (
        "custom_unit_list"
        if declared_scope != "all_units"
        else "all_units"
    )
    denom_source = (
        mapping.forced_denominator_source
        or _coerce_denominator_source(pool.denominator_source)
    )
    if mapping.promote_as_unresolved:
        denom_source = _coerce_denominator_source(pool.denominator_source)

    cur = connection.execute(
        """
        INSERT INTO allocation_pools (
            assessment_setup_id, pool_key, pool_name, denominator_label,
            declared_allocation_method, allocation_method, recipient_scope,
            denominator_source, denominator_value,
            variable_flag, display_order, include_in_pdf,
            budget_line_derivation,
            residual_after_pool_keys_json,
            residual_exclusions_json,
            pool_kind
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
        """,
        (
            setup_id, pool.pool_key, pool.pool_name or pool.pool_key,
            pool.denominator_label or None,
            pool.allocation_method,
            written_method, scope,
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


def _resolved_selected_unit_numbers(
    pool: AllocationPoolBlock,
    units: list[UnitRow],
) -> list[str]:
    """Return the exact reviewed/derived participant set for one pool."""
    mapping = map_allocation_method(pool.allocation_method)
    scope = mapping.forced_scope or _coerce_recipient_scope(pool.recipient_scope)
    if scope == "all_units":
        return []

    explicit = [
        str(value).strip()
        for value in pool.selected_unit_numbers
        if str(value).strip()
    ]
    if explicit:
        if len(set(explicit)) != len(explicit):
            raise InvalidStructuralOperation(
                "Category participant home identifiers must be distinct.",
                [pool.pool_key],
            )
        known = {str(unit.unit_number).strip() for unit in units}
        missing = [value for value in explicit if value not in known]
        if units and missing:
            raise InvalidStructuralOperation(
                "Category participant homes are not present in the unit schedule.",
                [pool.pool_key],
            )
        return explicit

    derived: list[str] = []
    for unit in units:
        unit_number = str(unit.unit_number).strip()
        if not unit_number:
            continue
        category = _coerce_category(
            unit.category,
            unit.residential_commercial_flag,
        )
        if scope == "residential_only" and category == "residential":
            derived.append(unit_number)
        elif scope == "commercial_only" and category == "commercial":
            derived.append(unit_number)
        elif scope == "parking_users" and _parking_count(unit.parking_flag) > 0:
            derived.append(unit_number)

    if not derived:
        raise InvalidStructuralOperation(
            "A non-all payer category has no evidenced or selected homes.",
            [pool.pool_key],
        )
    return derived


def _materialize_pool_participants(
    extraction: DRESetupExtraction,
) -> DRESetupExtraction:
    units = list(extraction.unit_structure.units)
    pools: list[AllocationPoolBlock] = []
    for pool in extraction.allocation_pools:
        mapping = map_allocation_method(pool.allocation_method)
        scope = mapping.forced_scope or _coerce_recipient_scope(
            pool.recipient_scope
        )
        selected = _resolved_selected_unit_numbers(pool, units)
        pools.append(
            pool.model_copy(
                update={
                    "recipient_scope": scope,
                    "selected_unit_numbers": selected,
                    "participant_unit_numbers": selected,
                }
            )
        )
    return extraction.model_copy(update={"allocation_pools": pools})


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

_MONEY_QUANTUM = Decimal("0.01")


class SpecifiedValueFactorValidation(BaseModel):
    valid: bool
    form: Optional[Literal["monthly", "annual"]] = None
    values: dict[str, Decimal] = Field(default_factory=dict)
    reason: Optional[str] = None
    failure_kind: Optional[Literal["missing", "invalid_total"]] = None


def validate_specified_value_factors(
    *,
    factors: dict[str, Decimal],
    participant_numbers: Iterable[str],
    annual_amount: Optional[Decimal],
    monthly_amount: Optional[Decimal],
) -> SpecifiedValueFactorValidation:
    """Validate one specified-value schedule using exact currency arithmetic."""
    participants = [str(value).strip() for value in participant_numbers]
    if not participants:
        return SpecifiedValueFactorValidation(
            valid=False,
            reason="no participating homes were resolved",
            failure_kind="missing",
        )
    values: dict[str, Decimal] = {}
    for unit_number in participants:
        value = factors.get(unit_number)
        if value is not None and value.is_finite() and value > 0:
            values[unit_number] = value
    if len(values) != len(participants):
        reason = (
            "extraction carried no per-unit dollar_amount factors"
            if not values
            else f"only {len(values)}/{len(participants)} units participating in "
            "this category carry a positive dollar_amount factor for this pool"
        )
        return SpecifiedValueFactorValidation(
            valid=False,
            values=values,
            reason=reason,
            failure_kind="missing",
        )

    total = sum(values.values(), start=Decimal("0"))

    def money_equal(left: Decimal, right: Optional[Decimal]) -> bool:
        return (
            right is not None
            and left.quantize(_MONEY_QUANTUM)
            == right.quantize(_MONEY_QUANTUM)
        )

    monthly_match = money_equal(total, monthly_amount) or money_equal(
        total * Decimal(12), annual_amount
    )
    annual_match = money_equal(total, annual_amount)
    if monthly_match != annual_match:
        return SpecifiedValueFactorValidation(
            valid=True,
            form="monthly" if monthly_match else "annual",
            values=values,
        )
    return SpecifiedValueFactorValidation(
        valid=False,
        values=values,
        reason=(
            f"per-home dollar factors sum to {total:,.2f}, which matches "
            f"{'both' if monthly_match else 'neither'} pool total "
            f"(monthly={monthly_amount}, annual={annual_amount})"
        ),
        failure_kind="invalid_total",
    )


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
    target_unit_numbers: Optional[list[str]] = None,
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
    target_unit_ids = (
        {
            unit_number: unit_id_by_number[unit_number]
            for unit_number in target_unit_numbers
            if unit_number in unit_id_by_number
        }
        if target_unit_numbers is not None
        else unit_id_by_number
    )
    if not target_unit_ids:
        return {"mode": "skipped", "form": None, "reason": "no units inserted"}

    validation = validate_specified_value_factors(
        factors=_dollar_factors_for_pool(units, pool_key),
        participant_numbers=target_unit_ids,
        annual_amount=annual_amount,
        monthly_amount=monthly_amount,
    )
    form = validation.form
    reason = validation.reason

    if validation.valid and form is not None:
        for unit_number, unit_id in target_unit_ids.items():
            value = validation.values[unit_number]
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
    target_count = len(target_unit_ids)
    if annual_amount is None or target_count <= 0:
        return {"mode": "skipped", "form": None, "reason": reason}
    logger.warning(
        "promotion: specified_value pool %r fell back to equal-split "
        "placeholder (%s); operator must enter per-unit values before "
        "package generation",
        pool_key,
        reason,
    )
    monthly = (annual_amount / Decimal(12) / Decimal(target_count)).quantize(
        Decimal("0.01")
    )
    for unit_number, unit_id in target_unit_ids.items():
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


_PROPORTIONAL_SQFT_METHODS = frozenset({"square_footage"})
_ISOLATED_POOL_KINDS = frozenset({"separately_billed_special_assessment"})


def _canonical_billing_treatment(pool: AllocationPoolBlock) -> str:
    if pool.amount_availability == "operator_pending":
        return "operator_amount_pending"
    if pool.billing_cadence == "one_time":
        return "separate_one_time"
    return "recurring"


def derive_ccr_pool_treatments(
    extraction: DRESetupExtraction,
) -> DRESetupExtraction:
    """Re-derive engine treatment from typed CCR pool semantics.

    Review edits can change cadence or amount availability after Gemini
    extraction. Recompute both legacy compatibility fields at the promotion
    boundary so stale ``billing_treatment`` or ``pool_kind`` values cannot
    contradict the independent semantics.
    """
    pools: list[AllocationPoolBlock] = []
    changed = False
    for pool in extraction.allocation_pools:
        pool_kind = (
            "separately_billed_special_assessment"
            if (
                pool.allocation_context == "special_assessment"
                and pool.billing_cadence == "one_time"
            )
            else ""
        )
        billing_treatment = _canonical_billing_treatment(pool)
        if (
            pool.pool_kind != pool_kind
            or pool.billing_treatment != billing_treatment
        ):
            pool = pool.model_copy(
                update={
                    "pool_kind": pool_kind,
                    "billing_treatment": billing_treatment,
                }
            )
            changed = True
        pools.append(pool)
    if not changed:
        return extraction
    return extraction.model_copy(update={"allocation_pools": pools})


def _is_isolated_structural_pool(pool: AllocationPoolBlock) -> bool:
    """Special-assessment / parking / cost-center pools keep their own basis."""
    if (pool.pool_kind or "") in _ISOLATED_POOL_KINDS:
        return True
    return _is_parking_or_cost_center_pool(pool)


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
            if _is_isolated_structural_pool(pool):
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
        if _is_isolated_structural_pool(pool):
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


def normalize_extraction_for_promotion(
    extraction: DRESetupExtraction,
) -> DRESetupExtraction:
    """Apply the pure normalization used immediately before promotion writes."""
    normalized = _normalize_proportional_pool_methods(extraction)
    return _fill_missing_square_footage_denominators(normalized)


def validate_ownership_percent_form(
    extraction: DRESetupExtraction,
) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """Resolve ownership column divisors without mutating or writing.

    The same validation is used by preview and by child population so an
    ambiguous ownership column is visible before approval starts writing.
    """
    has_ownership_pool = any(
        pool.allocation_method == "ownership_percentage"
        for pool in extraction.allocation_pools
    )
    forced_form = extraction.unit_structure.ownership_percent_form

    def _percent_divisor_for(rows: Iterable[Any], label: str) -> Optional[Decimal]:
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

    return (
        _percent_divisor_for(
            extraction.unit_structure.groups,
            "assessment_groups.ownership_percent",
        ),
        _percent_divisor_for(
            extraction.unit_structure.units,
            "assessment_units.ownership_percent",
        ),
    )


def validate_edited_entities_for_promotion(
    extraction: DRESetupExtraction,
    *,
    setup_type: str,
    edited_entity_keys: frozenset[str],
) -> list[str]:
    """Return edited entities that promotion would filter instead of insert."""
    normalized = normalize_extraction_for_promotion(extraction)
    failed: list[str] = []

    for pool in normalized.allocation_pools:
        entity_ref = f"pool:{pool.pool_key}"
        if entity_ref not in edited_entity_keys:
            continue
        mapping = map_allocation_method(pool.allocation_method)
        if mapping.internal_method is None and not mapping.promote_as_unresolved:
            failed.append(entity_ref)

    if setup_type == "grouped":
        for index, group in enumerate(normalized.unit_structure.groups):
            group_key = group.group_id or group.label or str(index)
            entity_ref = f"group:{group_key}"
            if (
                entity_ref in edited_entity_keys
                and (group.unit_count is None or group.unit_count <= 0)
            ):
                failed.append(entity_ref)

    if setup_type == "per_unit":
        for unit in normalized.unit_structure.units:
            entity_ref = f"unit:{unit.unit_number}"
            if entity_ref in edited_entity_keys and not unit.unit_number:
                failed.append(entity_ref)

    return failed


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
    extraction = normalize_extraction_for_promotion(extraction)
    extraction = _materialize_pool_participants(extraction)
    failed_edited_entities = validate_edited_entities_for_promotion(
        extraction,
        setup_type=setup_type,
        edited_entity_keys=edited_entity_keys,
    )
    if failed_edited_entities:
        raise EditedEntityFailedToPromote(failed_edited_entities)

    counts = {
        "pools": 0, "groups": 0, "units": 0, "unit_pool_allocations": 0,
    }

    # C8: resolve the ownership-percent column form ONCE, column-level,
    # before any row insert — the stored value is always the normalized
    # fraction. The operator's audited review decision
    # (unit_structure.ownership_percent_form) short-circuits the sum test.
    # An ambiguous column blocks promotion only when a pool actually
    # allocates by ownership percentage; display-only columns store
    # verbatim and the render-side resolver guards them.
    group_percent_divisor, unit_percent_divisor = validate_ownership_percent_form(
        extraction
    )

    pool_id_by_key: dict[str, int] = {}
    property_id_row = connection.execute(
        "SELECT property_id FROM assessment_setups WHERE id = ?",
        (setup_id,),
    ).fetchone()
    property_id = int(property_id_row[0]) if property_id_row else 0
    for idx, pool in enumerate(extraction.allocation_pools):
        pool_id = _insert_pool(
            setup_id=setup_id, pool=pool, display_order=idx, connection=connection,
        )
        if pool_id is not None:
            pool_id_by_key[pool.pool_key] = pool_id
            counts["pools"] += 1
            mapping = map_allocation_method(pool.allocation_method)
            try:
                seed_resolution_from_promotion(
                    connection,
                    property_id=property_id,
                    assessment_setup_id=setup_id,
                    pool_key=pool.pool_key,
                    declared_method=pool.allocation_method,
                    resolved_method=mapping.internal_method,
                    unresolved=bool(mapping.promote_as_unresolved),
                    denominator_label=pool.denominator_label or "",
                    included_categories=list(pool.included_budget_lines or []),
                    excluded_categories=list(pool.excluded_budget_lines or []),
                    source_pages=list(pool.source_pages or []),
                    source_text=pool.denominator_label or "",
                    denominator_value=pool.denominator_value,
                    denominator_source=_coerce_denominator_source(pool.denominator_source),
                )
            except sqlite3.OperationalError:
                logger.warning(
                    "promotion: allocation_resolutions table missing; "
                    "skipping resolution seed for %s",
                    pool.pool_key,
                )

    if setup_type == "grouped":
        for idx, group in enumerate(extraction.unit_structure.groups):
            if _insert_group(
                setup_id=setup_id, group=group,
                display_order=idx, connection=connection,
                percent_divisor=group_percent_divisor,
            ) is not None:
                counts["groups"] += 1
        _refresh_proportional_resolution_snapshots(
            setup_id=setup_id,
            extraction=extraction,
            connection=connection,
        )

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

        _promote_custom_factor_resolutions(
            setup_id=setup_id,
            extraction=extraction,
            connection=connection,
        )
        _refresh_proportional_resolution_snapshots(
            setup_id=setup_id,
            extraction=extraction,
            connection=connection,
        )

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
                target_unit_numbers=(
                    pool.selected_unit_numbers
                    if pool.recipient_scope != "all_units"
                    else None
                ),
            )
            if outcome["mode"] != "skipped":
                counts["unit_pool_allocations"] += (
                    len(pool.selected_unit_numbers)
                    if pool.recipient_scope != "all_units"
                    else before
                )
            if outcome["mode"] == "placeholder":
                counts.setdefault("specified_value_placeholders", []).append(
                    {"pool_key": pool.pool_key, "reason": outcome["reason"]}
                )
            _refresh_specified_resolution_snapshot(
                setup_id=setup_id,
                pool_key=pool.pool_key,
                connection=connection,
            )

    try:
        unresolved_row = connection.execute(
            """
            SELECT 1
              FROM allocation_resolutions
             WHERE assessment_setup_id = ?
               AND status IN ('unresolved', 'draft')
             LIMIT 1
            """,
            (setup_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE assessment_setups
               SET allocation_readiness_status = ?
             WHERE id = ?
            """,
            ("needs_review" if unresolved_row else "ok", setup_id),
        )
    except sqlite3.OperationalError:
        logger.warning(
            "promotion: allocation readiness column/table unavailable for setup %s",
            setup_id,
        )

    return counts


def _promote_custom_factor_resolutions(
    *,
    setup_id: int,
    extraction: DRESetupExtraction,
    connection: sqlite3.Connection,
) -> None:
    """Turn complete per-category custom factors into executable resolutions."""
    units_by_number = {
        str(unit.unit_number): unit for unit in extraction.unit_structure.units
    }
    for pool in extraction.allocation_pools:
        if pool.allocation_method != "custom_factor":
            continue
        participant_numbers = (
            list(units_by_number)
            if pool.recipient_scope == "all_units"
            else list(pool.selected_unit_numbers)
        )
        factors: dict[str, Decimal] = {}
        for unit_number in participant_numbers:
            unit = units_by_number.get(str(unit_number))
            if unit is None:
                break
            matches = [
                Decimal(str(factor.factor_value))
                for factor in unit.pool_factors
                if factor.pool_key == pool.pool_key
                and factor.factor_value is not None
                and Decimal(str(factor.factor_value)) > 0
            ]
            if len(matches) != 1:
                break
            factors[str(unit_number)] = matches[0]
        if len(factors) != len(participant_numbers) or not factors:
            continue

        total = sum(factors.values(), start=Decimal("0"))
        snapshot = {
            "method": "ownership_percentage",
            "denominator_value": str(total),
            "denominator_source": "manual",
            "recipients": {
                unit_number: str(value)
                for unit_number, value in factors.items()
            },
        }
        connection.execute(
            "UPDATE allocation_pools "
            "SET allocation_method = 'ownership_percentage', "
            "denominator_value = ?, denominator_source = 'manual' "
            "WHERE assessment_setup_id = ? AND pool_key = ?",
            (str(total), setup_id, pool.pool_key),
        )
        connection.execute(
            "UPDATE allocation_resolutions "
            "SET status = 'approved', resolved_method = 'ownership_percentage', "
            "denominator_value = ?, denominator_source = 'manual', "
            "factor_snapshot_json = ?, source = 'operator', "
            "approved_at = datetime('now') "
            "WHERE assessment_setup_id = ? AND pool_key = ? "
            "AND status IN ('unresolved', 'draft')",
            (str(total), json.dumps(snapshot), setup_id, pool.pool_key),
        )


def _refresh_proportional_resolution_snapshots(
    *,
    setup_id: int,
    extraction: DRESetupExtraction,
    connection: sqlite3.Connection,
) -> None:
    """Persist complete promoted factors for approved proportional rules.

    Promotion creates the resolution record while the pool is being inserted,
    before the unit rows exist.  Fill the snapshot after those rows are
    available, but never replace a non-empty operator snapshot.
    """
    try:
        resolution_rows = connection.execute(
            """
            SELECT pool_key, resolved_method, factor_snapshot_json
              FROM allocation_resolutions
             WHERE assessment_setup_id = ? AND status = 'approved'
            """,
            (setup_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return

    unit_rows = connection.execute(
        """
        SELECT unit_number, square_feet, ownership_percent
          FROM assessment_units
         WHERE assessment_setup_id = ?
         ORDER BY id
        """,
        (setup_id,),
    ).fetchall()
    group_rows = connection.execute(
        """
        SELECT group_name, unit_count, average_square_feet, ownership_percent
          FROM assessment_groups
         WHERE assessment_setup_id = ?
         ORDER BY id
        """,
        (setup_id,),
    ).fetchall()
    if not unit_rows and not group_rows:
        return
    pool_denominators = {
        str(row[0]): (
            Decimal(str(row[1])) if row[1] not in (None, "") else None
        )
        for row in connection.execute(
            """
            SELECT pool_key, denominator_value
              FROM allocation_pools
             WHERE assessment_setup_id = ?
            """,
            (setup_id,),
        ).fetchall()
    }

    payload_factor_values: dict[str, dict[str, Decimal]] = {}
    for unit in extraction.unit_structure.units:
        for factor in unit.pool_factors:
            try:
                value = Decimal(str(factor.factor_value))
            except (InvalidOperation, TypeError, ValueError):
                continue
            payload_factor_values.setdefault(str(factor.pool_key), {})[
                str(unit.unit_number)
            ] = value

    def _stored_snapshot(raw: Any) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            value = json.loads(str(raw))
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    for pool_key, method, raw_snapshot in resolution_rows:
        if method not in {"square_footage", "ownership_percentage"}:
            continue
        if _stored_snapshot(raw_snapshot).get("recipients"):
            continue

        denominator: Optional[Decimal] = None
        denominator_source: Optional[str] = None
        recipients: dict[str, Decimal] = {}
        if group_rows:
            if method == "square_footage":
                recipients = {
                    str(row[0]): Decimal(str(row[2])) * Decimal(str(row[1] or 1))
                    for row in group_rows
                    if row[2] not in (None, "")
                }
            else:
                raw_recipients = {
                    str(row[0]): Decimal(str(row[3]))
                    for row in group_rows
                    if row[3] not in (None, "")
                }
                weighted_recipients = {
                    str(row[0]): Decimal(str(row[3])) * Decimal(str(row[1] or 1))
                    for row in group_rows
                    if row[3] not in (None, "")
                }
                raw_total = sum(raw_recipients.values(), start=Decimal("0"))
                weighted_total = sum(
                    weighted_recipients.values(), start=Decimal("0")
                )
                recipients = (
                    raw_recipients
                    if abs(raw_total - Decimal("1"))
                    <= abs(weighted_total - Decimal("1"))
                    else weighted_recipients
                )
        else:
            if method == "square_footage":
                recipients = {
                    str(row[0]): Decimal(str(row[1]))
                    for row in unit_rows
                    if row[1] not in (None, "")
                }
            else:
                recipients = {
                    str(row[0]): Decimal(str(row[2]))
                    for row in unit_rows
                    if row[2] not in (None, "")
                }
                # Multi-factor DRE schedules carry a different ownership
                # column per category, not on assessment_units. Use that
                # category-specific column when the canonical unit column is
                # absent or incomplete.
                if len(recipients) != len(unit_rows):
                    recipients = payload_factor_values.get(str(pool_key), {})

        if len(recipients) != (len(group_rows) if group_rows else len(unit_rows)):
            continue
        if method == "square_footage":
            stored_denominator = pool_denominators.get(str(pool_key))
            denominator = stored_denominator or sum(
                recipients.values(), start=Decimal("0")
            )
            denominator_source = (
                "dre_value" if stored_denominator is not None else "calculated"
            )
            if denominator <= 0:
                continue
        elif sum(recipients.values(), start=Decimal("0")) <= 0:
            continue

        snapshot = {
            "method": method,
            "denominator_value": str(denominator) if denominator is not None else None,
            "denominator_source": denominator_source,
            "recipients": {key: str(value) for key, value in recipients.items()},
        }
        connection.execute(
            """
            UPDATE allocation_resolutions
               SET factor_snapshot_json = ?,
                   denominator_value = COALESCE(?, denominator_value),
                   denominator_source = COALESCE(?, denominator_source)
             WHERE assessment_setup_id = ?
               AND pool_key = ?
               AND status = 'approved'
            """,
            (
                json.dumps(snapshot, separators=(",", ":")),
                str(denominator) if denominator is not None else None,
                denominator_source,
                setup_id,
                pool_key,
            ),
        )


def _refresh_specified_resolution_snapshot(
    *,
    setup_id: int,
    pool_key: str,
    connection: sqlite3.Connection,
) -> None:
    """Persist promoted per-unit values as the approved resolution snapshot."""
    try:
        rows = connection.execute(
            """
            SELECT u.unit_number, a.specified_monthly_amount
              FROM assessment_unit_pool_allocations a
              JOIN assessment_units u ON u.id = a.assessment_unit_id
             WHERE a.assessment_setup_id = ? AND a.pool_key = ?
            """,
            (setup_id, pool_key),
        ).fetchall()
        if not rows:
            return
        snapshot = json.dumps({
            "method": "specified_value",
            "denominator_value": None,
            "denominator_source": None,
            "recipients": {str(row[0]): str(row[1]) for row in rows},
        })
        connection.execute(
            """
            UPDATE allocation_resolutions
               SET factor_snapshot_json = ?,
                   denominator_value = NULL,
                   denominator_source = NULL
             WHERE assessment_setup_id = ?
               AND pool_key = ?
               AND status = 'approved'
            """,
            (snapshot, setup_id, pool_key),
        )
    except sqlite3.OperationalError:
        # Legacy databases can promote before the allocation-resolution tables
        # have been created; their normal specified-value lookup remains valid.
        logger.warning(
            "promotion: could not refresh specified-value resolution snapshot for %s",
            pool_key,
        )


__all__ = [
    "EditedEntityFailedToPromote",
    "MissingUnitFactors",
    "UnresolvableReviewEdit",
    "apply_review_edits_to_extraction",
    "check_missing_unit_factors",
    "derive_ccr_pool_treatments",
    "entity_keys_touched_by_edits",
    "parse_extraction_payload",
    "populate_setup_children",
]
