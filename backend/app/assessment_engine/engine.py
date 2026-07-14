"""Calculation orchestrator.

Combines pool math, recipient resolution, and the per-recipient
summation/rounding/reconciliation flow into a single ``run(calc_input)``
entry point. Pure function — no DB, no I/O. The Phase 1.3 resolver is
what builds ``CalcInput`` from ``(assessment_setup_id, budget_year)``.

The 8-step flow (per spec §Calculation Flow):
    1. Route budget lines to pools via BudgetLinePoolMapping.
    2. For each pool: convert annual→monthly at the boundary, resolve
       recipients by scope, dispatch to the appropriate allocator,
       record PoolAllocationResult rows at full Decimal precision.
    3. Apply pool-scope overrides (rewrite pool_allocations with
       source='override').
    4. Sum components per recipient → raw_monthly_total.
    5. Round once at the recipient level → rounded_monthly_total.
    6. Apply package/group/unit-scope overrides (replace
       rounded_monthly_total).
    7. Compute annual_total = rounded × 12 × unit_count.
    8. Compute reconciliation deltas against
       AnnualPackage.approved_assessment_revenue_annual.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Literal, Optional

from .errors import NeedsHumanReview, UnsupportedAllocationMethod
from .pools import (
    equal_allocation,
    ownership_percentage_allocation,
    specified_value_allocation,
    square_footage_allocation,
)
from .recipients import resolve_recipients
from .schemas import (
    AppliedOverrideEntry,
    AssessmentOverride,
    BudgetLineInput,
    BudgetLineMappingInput,
    CalcInput,
    CalcResultSet,
    OrphanedPoolReport,
    PoolAllocationResult,
    PoolDefinition,
    RecipientReference,
    RecipientTotalResult,
    SpecialAssessmentAllocation,
    SpecialAssessmentAllocationEntry,
    SpecialAssessmentInput,
    SpecialAssessmentRendererEvent,
    ZeroRecipientPoolReport,
)

SPECIAL_ASSESSMENT_POOL_KIND = "separately_billed_special_assessment"


CENTS = Decimal("0.01")
TWELVE = Decimal("12")


# -- step 1: route budget lines to pools -----------------------------------


def _mapping_key(
    label: str,
    section: str,
    category: str,
    fund_type: str,
    account_code: str | None,
) -> tuple[str, str, str, str, str | None]:
    return (label, section, category, fund_type, account_code)


def _aggregate_by_pool(
    budget_lines: Iterable[BudgetLineInput],
    mappings: Iterable[BudgetLineMappingInput],
) -> tuple[dict[str, Decimal], dict[str, list[str]]]:
    """Sum annual budget-line amounts by ``pool_key``.

    Raises ``NeedsHumanReview`` for any line lacking an ``active=True``
    BudgetLinePoolMapping. Inactive mappings are ignored.

    Returns ``(pool_totals, contributing_labels)`` where
    ``contributing_labels`` maps each ``pool_key`` to the normalized labels
    of the budget lines that fed it — used to name the specific lines when
    a pool_key turns out to be orphaned (H1) or its pool has zero recipients
    (H2).
    """
    routing: dict[tuple, str] = {}
    for m in mappings:
        if not m.active:
            continue
        key = _mapping_key(
            m.budget_line_normalized_label,
            m.section,
            m.category,
            m.fund_type,
            m.account_code,
        )
        routing[key] = m.pool_key

    pool_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    contributing_labels: dict[str, list[str]] = defaultdict(list)
    for line in budget_lines:
        key = _mapping_key(
            line.normalized_label,
            line.section,
            line.category,
            line.fund_type,
            line.account_code,
        )
        if key not in routing:
            raise NeedsHumanReview(line)
        pool_key = routing[key]
        pool_totals[pool_key] += line.amount
        contributing_labels[pool_key].append(line.normalized_label)

    return dict(pool_totals), dict(contributing_labels)


# -- step 2: per-pool dispatch ---------------------------------------------


def _allocate_pool(
    pool: PoolDefinition,
    monthly_total: Decimal,
    recipients: list[RecipientReference],
    specified_value_lookup: dict[tuple[int, str], Decimal],
) -> tuple[list[PoolAllocationResult], list[str]]:
    """Run the right allocator for one pool. Returns
    (pool_allocation_rows, warnings).
    """
    warnings: list[str] = []
    rows: list[PoolAllocationResult] = []

    if pool.allocation_method == "equal":
        total_units = sum((r.unit_count for r in recipients), start=0)
        per_unit = equal_allocation(monthly_total, total_units)
        for r in recipients:
            rows.append(
                PoolAllocationResult(
                    recipient_ref=r,
                    pool_id=pool.pool_id,
                    pool_key=pool.pool_key,
                    unrounded_component_monthly=per_unit * Decimal(r.unit_count),
                )
            )
        return rows, warnings

    if pool.allocation_method == "square_footage":
        if pool.denominator_value is None:
            raise UnsupportedAllocationMethod(
                "square_footage (denominator missing)", pool.pool_key
            )
        recomputed = sum(
            (
                r.square_feet * Decimal(r.unit_count)
                for r in recipients
                if r.square_feet is not None
            ),
            start=Decimal("0"),
        )
        if recomputed and recomputed != pool.denominator_value:
            warnings.append(
                f"DenominatorMismatchWarning: pool '{pool.pool_key}' "
                f"DRE-frozen denominator={pool.denominator_value} differs "
                f"from current recipient sum={recomputed} "
                f"(delta={recomputed - pool.denominator_value}); "
                "engine used DRE value verbatim"
            )
        per_unit_within_group = square_footage_allocation(
            monthly_total, pool.denominator_value, recipients
        )
        # Normalize sqft components to per-recipient (per-group for groups,
        # per-unit for units). The allocator returns per-unit-within-group
        # because group.square_feet is avg-per-unit; multiplying by unit_count
        # collapses to per-group dollars. For unit recipients, unit_count=1
        # so this is a no-op.
        for r in recipients:
            per_recipient_value = (
                per_unit_within_group[(r.ref_type, r.ref_id)] * Decimal(r.unit_count)
            )
            rows.append(
                PoolAllocationResult(
                    recipient_ref=r,
                    pool_id=pool.pool_id,
                    pool_key=pool.pool_key,
                    unrounded_component_monthly=per_recipient_value,
                )
            )
        return rows, warnings

    if pool.allocation_method == "ownership_percentage":
        per_recipient, pool_warnings = ownership_percentage_allocation(
            monthly_total, recipients
        )
        warnings.extend(pool_warnings)
        for r in recipients:
            rows.append(
                PoolAllocationResult(
                    recipient_ref=r,
                    pool_id=pool.pool_id,
                    pool_key=pool.pool_key,
                    unrounded_component_monthly=per_recipient[(r.ref_type, r.ref_id)],
                )
            )
        return rows, warnings

    if pool.allocation_method == "specified_value":
        per_recipient = specified_value_allocation(
            pool.pool_key, recipients, specified_value_lookup
        )
        for r in recipients:
            rows.append(
                PoolAllocationResult(
                    recipient_ref=r,
                    pool_id=pool.pool_id,
                    pool_key=pool.pool_key,
                    unrounded_component_monthly=per_recipient[(r.ref_type, r.ref_id)],
                )
            )
        return rows, warnings

    raise UnsupportedAllocationMethod(pool.allocation_method, pool.pool_key)


# -- step 3.5: special-assessment behavior matrix (Phase 4.4) --------------


def _resolve_allocation_pool_for_scope(
    pools: list[PoolDefinition],
    recipient_scope: str,
) -> Optional[PoolDefinition]:
    """Pick the pool whose ``recipient_scope`` matches a special
    assessment's scope, so its allocation method can be reused.

    If more than one pool shares the scope, the lowest ``display_order``
    wins (deterministic, matches the pool's on-page ordering). Returns
    ``None`` if no pool covers this scope at all.
    """
    candidates = [p for p in pools if p.recipient_scope == recipient_scope]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.display_order)[0]


def _allocate_special_assessment(
    total: Decimal,
    recipients,
    pool: Optional[PoolDefinition],
    method: str,
    *,
    scope: str = "",
    label: str = "",
) -> tuple[dict[tuple[str, int], Decimal], list[str]]:
    """Allocate a special-assessment ``total`` across ``recipients`` by
    ``method`` (borrowed from ``pool``), reusing the same allocation functions
    as regular pool math. Returns unrounded per-recipient component amounts keyed
    by ``(ref_type, ref_id)`` plus warnings — rounding stays per-recipient at
    summation time.

    Shared by the settings-json path (``_apply_special_assessments``, where
    ``total`` is a monthly figure) and the pool-based path (``run()``, where
    ``total`` is a one-time lump). The caller decides the meaning of ``total``;
    this helper never divides by 12.
    """
    warnings: list[str] = []
    result: dict[tuple[str, int], Decimal] = {}
    total_units = sum((r.unit_count for r in recipients), start=0)

    if method == "square_footage" and pool is not None and pool.denominator_value is not None:
        per_unit_within_group = square_footage_allocation(
            total, pool.denominator_value, recipients
        )
        for r in recipients:
            result[(r.ref_type, r.ref_id)] = (
                per_unit_within_group[(r.ref_type, r.ref_id)] * Decimal(r.unit_count)
            )
    elif method == "ownership_percentage":
        per_recipient, pct_warnings = ownership_percentage_allocation(total, recipients)
        warnings.extend(pct_warnings)
        for r in recipients:
            result[(r.ref_type, r.ref_id)] = per_recipient[(r.ref_type, r.ref_id)]
    else:
        # Equal, or a method that can't be generalized to an arbitrary
        # special-assessment total (specified_value is a per-budget-line absolute
        # dollar lookup, not a proportional weight; square_footage without a
        # denominator can't split). Fall back to equal-per-unit rather than guess.
        if method not in ("equal",) and pool is not None:
            warnings.append(
                f"SpecialAssessmentAllocationFallbackWarning: recipient_scope "
                f"'{scope}' resolves to a '{method}' pool, which cannot be "
                f"generalized to an arbitrary special-assessment total; special "
                f"assessment '{label}' fell back to equal-per-unit allocation"
            )
        per_unit = equal_allocation(total, total_units)
        for r in recipients:
            result[(r.ref_type, r.ref_id)] = per_unit * Decimal(r.unit_count)
    return result, warnings


def _apply_special_assessments(
    pool_allocations: list[PoolAllocationResult],
    special_assessments: list[SpecialAssessmentInput],
    recipient_set,
    pools: list[PoolDefinition],
) -> tuple[list[SpecialAssessmentRendererEvent], list[str]]:
    """Apply each SA entry per the Phase 4.4 matrix.

    - ``approved_scheduled`` + ``included_in_regular_monthly=True``:
      append one pseudo-pool component row per applicable recipient with
      ``pool_id=None``, ``pool_key='special_assessment'``,
      ``source='special_assessment'``. These flow into the regular
      recipient_total → reconciliation path. The per-recipient split
      uses the HOA's real allocation method (equal / square_footage /
      ownership_percentage), borrowed from whichever existing pool
      covers the same ``recipient_scope`` — matching how regular
      monthly assessments are already split, instead of assigning every
      recipient the same flat ``amount_per_unit``.
    - ``approved_scheduled`` + ``included_in_regular_monthly=False``:
      emit a ``separate_disclosure_block`` renderer event; engine
      doesn't touch pool_allocations or reconciliation. (Unchanged —
      this one-time-disclosure display path is a separate, deferred
      piece of work; see design.md.)
    - ``possible_disclosure_only``: emit a ``disclosure_language``
      renderer event; engine takes no financial action.
    - ``none``: ignored.

    Mutates ``pool_allocations`` in place; returns
    ``(renderer_events, warnings)``.
    """
    events: list[SpecialAssessmentRendererEvent] = []
    warnings: list[str] = []
    for entry in special_assessments:
        if entry.status == "none":
            continue

        if entry.status == "approved_scheduled":
            if not entry.included_in_regular_monthly:
                events.append(
                    SpecialAssessmentRendererEvent(
                        kind="separate_disclosure_block",
                        label=entry.label,
                        amount_per_unit=entry.amount_per_unit,
                        due_date=entry.due_date,
                    )
                )
                continue

            scope = entry.recipient_scope or "all_units"
            recipients = resolve_recipients(
                recipient_set,
                scope,
                custom_unit_ids=entry.custom_unit_ids or None,
            )
            total_units = sum((r.unit_count for r in recipients), start=0)
            if not recipients or total_units <= 0:
                continue

            # ``amount_per_unit`` has always meant "this much per unit per
            # month" (mirrors how regular pool totals are annual and get
            # divided to monthly at the pool boundary). Reconstructing the
            # scope-wide monthly total this way — instead of taking
            # ``amount_per_unit`` as gospel per recipient — is what lets a
            # non-equal method redistribute it correctly while leaving the
            # equal-allocation, unit-grain case byte-identical to today.
            monthly_total = entry.amount_per_unit * Decimal(total_units)

            pool = _resolve_allocation_pool_for_scope(pools, scope)
            method = pool.allocation_method if pool is not None else "equal"

            if pool is None:
                warnings.append(
                    f"SpecialAssessmentNoAllocationDataWarning: no allocation_pools "
                    f"entry found for recipient_scope '{scope}'; special assessment "
                    f"'{entry.label or ''}' fell back to equal-per-unit allocation"
                )

            allocation, alloc_warnings = _allocate_special_assessment(
                monthly_total, recipients, pool, method,
                scope=scope, label=entry.label or "",
            )
            warnings.extend(alloc_warnings)
            for r in recipients:
                pool_allocations.append(
                    PoolAllocationResult(
                        recipient_ref=r,
                        pool_id=None,
                        pool_key="special_assessment",
                        unrounded_component_monthly=allocation[(r.ref_type, r.ref_id)],
                        source="special_assessment",
                    )
                )

        elif entry.status == "possible_disclosure_only":
            events.append(
                SpecialAssessmentRendererEvent(
                    kind="disclosure_language",
                    label=entry.label,
                    display_language=entry.display_language,
                )
            )

    return events, warnings


# -- step 3: pool-scope overrides ------------------------------------------


def _apply_pool_overrides(
    pool_allocations: list[PoolAllocationResult],
    overrides: Iterable[AssessmentOverride],
    applied_overrides: list[AppliedOverrideEntry],
) -> list[PoolAllocationResult]:
    """Rewrite every component row in an overridden pool to
    ``source='override'`` with the operator-supplied monthly amount.

    Audit: appends one ``AppliedOverrideEntry`` per pool-scoped
    override into ``applied_overrides`` capturing the pre-override
    monthly total (sum across recipients) so the operator UI can show
    the delta.
    """
    pool_overrides = {
        o.scope_ref_id: o for o in overrides if o.scope == "pool" and o.scope_ref_id is not None
    }
    if not pool_overrides:
        return pool_allocations

    pre_override_totals: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in pool_allocations:
        if row.pool_id in pool_overrides:
            pre_override_totals[row.pool_id] += row.unrounded_component_monthly

    out: list[PoolAllocationResult] = []
    for row in pool_allocations:
        ov = pool_overrides.get(row.pool_id)
        if ov is not None:
            out.append(
                row.model_copy(
                    update={
                        "unrounded_component_monthly": ov.override_monthly_amount,
                        "source": "override",
                    }
                )
            )
        else:
            out.append(row)

    for pool_id, ov in pool_overrides.items():
        original = pre_override_totals.get(pool_id)
        applied_overrides.append(
            AppliedOverrideEntry(
                scope="pool",
                scope_ref_id=pool_id,
                override_type=ov.override_type,
                original_calculated_monthly=original,
                override_monthly=ov.override_monthly_amount,
                delta_monthly=(
                    ov.override_monthly_amount - original
                    if original is not None
                    else None
                ),
                reason=ov.reason,
                approved_by=ov.approved_by,
            )
        )

    return out


# -- steps 4–5: per-recipient summation + rounding -------------------------


def _round_cents(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _summarize_recipients(
    pool_allocations: list[PoolAllocationResult],
) -> list[RecipientTotalResult]:
    """Sum components per recipient, round once, compute annual total.

    Recipients appear in the order their first PoolAllocationResult row
    appeared — preserves stable output for templates.
    """
    components_by_recipient: dict[tuple[str, int], Decimal] = defaultdict(
        lambda: Decimal("0")
    )
    recipient_ref_by_key: dict[tuple[str, int], RecipientReference] = {}
    insertion_order: list[tuple[str, int]] = []

    for row in pool_allocations:
        key = (row.recipient_ref.ref_type, row.recipient_ref.ref_id)
        if key not in recipient_ref_by_key:
            recipient_ref_by_key[key] = row.recipient_ref
            insertion_order.append(key)
        components_by_recipient[key] += row.unrounded_component_monthly

    totals: list[RecipientTotalResult] = []
    for key in insertion_order:
        r = recipient_ref_by_key[key]
        raw = components_by_recipient[key]
        rounded = _round_cents(raw)
        # All pool components are normalized to per-recipient dollars
        # (per-group for groups, per-unit for units) inside _allocate_pool.
        # Annual is therefore rounded × 12 with no further multiplication.
        annual = rounded * TWELVE
        totals.append(
            RecipientTotalResult(
                recipient_ref=r,
                raw_monthly_total=raw,
                rounded_monthly_total=rounded,
                annual_total=annual,
                rounding_delta_contribution=Decimal("0"),
            )
        )
    return totals


# -- step 6: package / group / unit overrides ------------------------------


def _apply_recipient_overrides(
    totals: list[RecipientTotalResult],
    overrides: Iterable[AssessmentOverride],
    applied_overrides: list[AppliedOverrideEntry],
) -> list[RecipientTotalResult]:
    """Apply package/group/unit-scope overrides.

    - ``package``: every recipient's rounded_monthly_total is replaced.
    - ``group`` / ``unit``: only matching recipients are replaced.

    Annual totals are recomputed after each override.

    Audit: appends one ``AppliedOverrideEntry`` per override actually
    used (package override fires per matching recipient; group/unit
    overrides fire per matching scope_ref_id).
    """
    package_override: AssessmentOverride | None = next(
        (o for o in overrides if o.scope == "package"), None
    )
    group_overrides = {
        o.scope_ref_id: o
        for o in overrides
        if o.scope == "group" and o.scope_ref_id is not None
    }
    unit_overrides = {
        o.scope_ref_id: o
        for o in overrides
        if o.scope == "unit" and o.scope_ref_id is not None
    }

    if not (package_override or group_overrides or unit_overrides):
        return totals

    package_audit_done = False
    out: list[RecipientTotalResult] = []
    for t in totals:
        new_monthly: Decimal | None = None
        fired: tuple[Literal["package", "group", "unit"], Optional[int], AssessmentOverride] | None = None
        if package_override is not None:
            new_monthly = package_override.override_monthly_amount
            fired = ("package", None, package_override)
        if t.recipient_ref.ref_type == "group" and t.recipient_ref.ref_id in group_overrides:
            ov = group_overrides[t.recipient_ref.ref_id]
            new_monthly = ov.override_monthly_amount
            fired = ("group", t.recipient_ref.ref_id, ov)
        if t.recipient_ref.ref_type == "unit" and t.recipient_ref.ref_id in unit_overrides:
            ov = unit_overrides[t.recipient_ref.ref_id]
            new_monthly = ov.override_monthly_amount
            fired = ("unit", t.recipient_ref.ref_id, ov)

        if new_monthly is None:
            out.append(t)
            continue

        rounded = _round_cents(new_monthly)
        annual = rounded * TWELVE
        out.append(
            t.model_copy(
                update={
                    "rounded_monthly_total": rounded,
                    "annual_total": annual,
                }
            )
        )

        if fired is not None:
            scope, ref_id, ov = fired
            # Package override applies to every recipient; only audit once
            if scope == "package":
                if package_audit_done:
                    continue
                package_audit_done = True
                original = None  # package override doesn't capture per-recipient pre-state
            else:
                original = t.rounded_monthly_total
            applied_overrides.append(
                AppliedOverrideEntry(
                    scope=scope,
                    scope_ref_id=ref_id,
                    override_type=ov.override_type,
                    original_calculated_monthly=original,
                    override_monthly=ov.override_monthly_amount,
                    delta_monthly=(
                        ov.override_monthly_amount - original
                        if original is not None
                        else None
                    ),
                    reason=ov.reason,
                    approved_by=ov.approved_by,
                )
            )
    return out


# -- step 7: reconciliation ------------------------------------------------


def _reconcile(
    totals: list[RecipientTotalResult],
    approved_revenue: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    total_calculated = sum(
        (t.annual_total for t in totals), start=Decimal("0")
    )
    delta_annual = total_calculated - approved_revenue
    delta_monthly = delta_annual / TWELVE
    delta_percent = (
        delta_annual / approved_revenue
        if approved_revenue != 0
        else Decimal("0")
    )
    return delta_annual, delta_monthly, delta_percent


# -- public entry point ----------------------------------------------------


def run(calc_input: CalcInput) -> CalcResultSet:
    """Run the full calculation flow against a pre-resolved CalcInput.

    Returns a ``CalcResultSet`` with pool_allocations, recipient_totals,
    rounding deltas, pool_sum diagnostic, and warnings.
    """
    pool_totals_annual, contributing_labels = _aggregate_by_pool(
        calc_input.budget_lines, calc_input.mappings
    )

    pool_allocations: list[PoolAllocationResult] = []
    warnings: list[str] = []
    pool_sum_annual = Decimal("0")
    zero_recipient_pools: list[ZeroRecipientPoolReport] = []
    special_assessment_allocations: list[SpecialAssessmentAllocation] = []

    defined_pool_keys = {pool.pool_key for pool in calc_input.pools}

    for pool in sorted(calc_input.pools, key=lambda p: p.display_order):
        annual_total = pool_totals_annual.get(pool.pool_key, Decimal("0"))

        if pool.pool_kind == SPECIAL_ASSESSMENT_POOL_KIND:
            # Separately-billed special assessment: allocate the pool's total
            # ONCE (a one-time lump — no ÷12) across recipients by the pool's
            # basis, and keep it OUT of pool_sum_annual / recipient_totals /
            # reconciliation. It renders as its own allocation table, never as
            # monthly dues. (An empty total → zero entries; preflight flags it.)
            sa_custom_ids = calc_input.pool_custom_recipients.get(pool.pool_key)
            sa_recipients = resolve_recipients(
                calc_input.recipient_set,
                pool.recipient_scope,
                custom_unit_ids=sa_custom_ids,
            )
            if not sa_recipients:
                if annual_total != 0:
                    zero_recipient_pools.append(
                        ZeroRecipientPoolReport(
                            pool_key=pool.pool_key,
                            recipient_scope=pool.recipient_scope,
                            annual_total=annual_total,
                            contributing_line_labels=contributing_labels.get(
                                pool.pool_key, []
                            ),
                        )
                    )
                continue
            sa_allocation, sa_warnings = _allocate_special_assessment(
                annual_total, sa_recipients, pool, pool.allocation_method,
                scope=pool.recipient_scope, label=pool.pool_name,
            )
            warnings.extend(sa_warnings)
            special_assessment_allocations.append(
                SpecialAssessmentAllocation(
                    pool_key=pool.pool_key,
                    label=pool.pool_name,
                    allocation_method=pool.allocation_method,
                    total=annual_total,
                    entries=[
                        SpecialAssessmentAllocationEntry(
                            recipient_ref=r,
                            amount=sa_allocation[(r.ref_type, r.ref_id)],
                        )
                        for r in sa_recipients
                    ],
                )
            )
            continue

        pool_sum_annual += annual_total
        monthly_total = annual_total / TWELVE

        custom_ids = calc_input.pool_custom_recipients.get(pool.pool_key)
        recipients = resolve_recipients(
            calc_input.recipient_set,
            pool.recipient_scope,
            custom_unit_ids=custom_ids,
        )
        if not recipients:
            # H2: a pool with nonzero mapped dollars but zero resolved
            # recipients. Skipping allocation is still correct (there is
            # nobody to bill), but do it loudly — record the pool, its
            # scope, its dollar total, and the lines feeding it so the
            # caller can surface a resolvable review row instead of the
            # money vanishing silently.
            if annual_total != 0:
                zero_recipient_pools.append(
                    ZeroRecipientPoolReport(
                        pool_key=pool.pool_key,
                        recipient_scope=pool.recipient_scope,
                        annual_total=annual_total,
                        contributing_line_labels=contributing_labels.get(
                            pool.pool_key, []
                        ),
                    )
                )
            continue

        rows, pool_warnings = _allocate_pool(
            pool,
            monthly_total,
            recipients,
            calc_input.specified_value_lookup,
        )
        pool_allocations.extend(rows)
        warnings.extend(pool_warnings)

    # H1: pool_keys that budget lines route dollars to but which have no
    # PoolDefinition in this setup. Their dollars never entered the loop
    # above (never allocated, never in pool_sum_annual). Report them so the
    # caller can name the exact lines and offer remap/exclude.
    orphaned_pool_lines: list[OrphanedPoolReport] = [
        OrphanedPoolReport(
            pool_key=pool_key,
            annual_total=annual_total,
            contributing_line_labels=contributing_labels.get(pool_key, []),
        )
        for pool_key, annual_total in pool_totals_annual.items()
        if pool_key not in defined_pool_keys
    ]

    # Step 3.5: special-assessment behavior matrix (Phase 4.4).
    # ``approved_scheduled`` + ``included_in_regular_monthly`` adds
    # pseudo-pool rows so they flow through recipient summation +
    # reconciliation just like any other pool component. The other
    # branches emit renderer events that bypass financial math.
    special_assessment_events, special_assessment_warnings = _apply_special_assessments(
        pool_allocations,
        calc_input.special_assessments,
        calc_input.recipient_set,
        calc_input.pools,
    )
    warnings.extend(special_assessment_warnings)

    applied_overrides: list[AppliedOverrideEntry] = []
    pool_allocations = _apply_pool_overrides(
        pool_allocations, calc_input.overrides, applied_overrides
    )

    recipient_totals = _summarize_recipients(pool_allocations)
    recipient_totals = _apply_recipient_overrides(
        recipient_totals, calc_input.overrides, applied_overrides
    )

    delta_annual, delta_monthly, delta_percent = _reconcile(
        recipient_totals, calc_input.approved_assessment_revenue_annual
    )

    return CalcResultSet(
        pool_allocations=pool_allocations,
        recipient_totals=recipient_totals,
        rounding_delta_annual=delta_annual,
        rounding_delta_monthly=delta_monthly,
        rounding_delta_percent=delta_percent,
        pool_sum_annual=pool_sum_annual,
        warnings=warnings,
        special_assessment_events=special_assessment_events,
        special_assessment_allocations=special_assessment_allocations,
        applied_overrides=applied_overrides,
        orphaned_pool_lines=orphaned_pool_lines,
        zero_recipient_pools=zero_recipient_pools,
    )
