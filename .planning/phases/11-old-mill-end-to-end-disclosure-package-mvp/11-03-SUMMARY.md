---
phase: 11
plan: 03
subsystem: disclosure_package
tags: [phase-11, adapters, preflight, input-boundary, decimal-math, duck-typing]
requires: [11-01, 11-02]
provides:
  - "backend/app/disclosure_package/adapters.py — from_budget_history_record / from_reserve_study_extraction / from_hoa_record (Phase 4/7 + Phase 10 + Property ORM → typed schemas)"
  - "backend/app/disclosure_package/preflight.py — validate_inputs() returning list[PreflightError] with stable field paths matching UI-SPEC §9.3"
  - "backend/tests/test_disclosure_package_adapters.py — 8 RED→GREEN tests"
  - "backend/tests/test_disclosure_package_preflight.py — 8 RED→GREEN tests"
affects: []
tech_stack_added: []
patterns_established:
  - "_to_decimal(value) helper: single chokepoint for float→Decimal(str()) coercion (T-11-04, RESEARCH Pitfall 2)"
  - "_attr_or_key(obj, name, default) helper: duck-typed attribute-or-dict reader so the reserve-study adapter tolerates Phase 10 schema evolution without rippling across the boundary (RESEARCH Risk #3)"
  - "Field-path strings as the UI-frontend contract: preflight.py emits the exact field_path values DisclosurePreflightChecklist reads to render row labels"
  - "appendices_root parameter pattern: pure-function default (None) skips I/O; caller opts in to the filesystem check by passing a Path"
key_files_created:
  - backend/app/disclosure_package/adapters.py
  - backend/app/disclosure_package/preflight.py
  - backend/tests/test_disclosure_package_adapters.py
  - backend/tests/test_disclosure_package_preflight.py
key_files_modified: []
decisions:
  - "Adapters never import from app.models — duck typing via _attr_or_key supports both attribute access (Pydantic / SQLAlchemy / SimpleNamespace) and dict access (raw service payloads). CONTEXT D-03 verified by `grep -E 'from (app\\.models|\\.\\.models)' backend/app/disclosure_package/*.py` returning zero matches."
  - "Preflight uses model_construct() in tests to bypass Pydantic's own min_length/range validators when simulating an upstream caller that built objects without validation. Defense-in-depth: even if the schema layer is bypassed, preflight catches the gate violation."
  - "appendices_root is keyword-only and defaults to None — unit tests pass None to keep validate_inputs pure (no I/O); the runtime caller (plan 11-06 router/service) passes the resolved appendices directory."
  - "Reserve-study adapter skips rows where useful_life is None or 0, plus rows where remaining_life or replacement_cost is None. These rows can't enter the formula DAG (division-by-zero in year_replacement_provision) and are typically header rows or extraction artifacts."
metrics:
  tasks_completed: 2
  tasks_total: 2
  duration: "~10 min"
  files_created: 4
  files_modified: 0
  test_count: 16
  test_runtime_seconds: 0.11
  commits:
    - "d5de78d feat(11-03): add input adapters — BudgetHistoryRecord/ReserveStudy/Property → typed schemas"
    - "8faea2e feat(11-03): preflight validator with stable field paths matching UI-SPEC §9.3"
completed_date: "2026-05-08"
---

# Phase 11 Plan 03: Input Adapters + Preflight Gate Summary

Closed the Phase 11 input boundary: existing service shapes
(`BudgetHistoryRecord`, `ExtractedReserveStudyDocument`, `Property` ORM
row) now map cleanly into the typed `disclosure_package` schemas via
three pure functions, and a preflight validator emits structured
`list[PreflightError]` with field paths matching UI-SPEC §9.3 verbatim.
The compiler still has zero `from app.models` imports (CONTEXT D-03
verified by grep), preserving the boundary that lets Phase 12+ add new
HOAs without touching `adapters.py`.

## Tasks Executed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | Adapter functions — service shapes → typed schemas | `d5de78d` | backend/app/disclosure_package/adapters.py, backend/tests/test_disclosure_package_adapters.py |
| 2 | Preflight validator — structured errors with stable field paths | `8faea2e` | backend/app/disclosure_package/preflight.py, backend/tests/test_disclosure_package_preflight.py |

## Adapter contract recap

| Function | Input shape | Output | Notes |
|----------|-------------|--------|-------|
| `from_budget_history_record(record)` | dict OR `BudgetHistoryRecord`/`BudgetDraftPayload` with `line_items: list[dict]` | `BudgetDraft` | Decimal money via `_to_decimal`; Phase 7 flags pass through (Pitfall 3); empty `line_items` → `ValueError` |
| `from_reserve_study_extraction(document)` | duck-typed (Phase 10 `ExtractedReserveStudyDocument`, dict, or anything with `study_date`/`rows`) | `ReserveStudySnapshot` | Skips rows where `useful_life` is None/0 or `remaining_life`/`replacement_cost` is None |
| `from_hoa_record(property_row)` | duck-typed (`Property` ORM row, dict, or `SimpleNamespace`) | `HOAMetadata` | `units` non-positive → `ValueError`; defaults `fiscal_year_start_month=1`, `fiscal_year_end_month=12` |

## Preflight contract recap

`validate_inputs(*, spec, budget_draft, reserve_snapshot, hoa_metadata, appendices_root=None) -> list[PreflightError]`

Gate evaluation order (deterministic, asserted by test 7):

1. `budget_draft.line_items` — empty → blocking (REQ-D11-005)
2. `reserve_study_snapshot.components` — empty → blocking
3. `hoa_metadata.fiscal_year_end_month` — None or out of 1-12 → blocking
4. `reserve_cash_balance.amount` — `spec.static_data.reserve_cash_balance_eoy_prior <= 0` → blocking (REQ-D11-004)
5. `package_spec.appendices` — only when `appendices_root is not None`; missing `StaticAppendix.file` on disk → blocking (one error per missing file)

Empty list = ready to render. `appendices_root=None` keeps the function
I/O-free for unit tests and for the staged validation pattern in plan
11-06's router (the FS check is deferred to right before merge in that
flow).

## Existing-service shape findings

Per the plan's `<output>` section: were any unexpected field shapes encountered?

**No surprises.** The existing service shapes match the RESEARCH /
PATTERNS prediction exactly:

* `BudgetDraftPayload.line_items` is `list[dict[str, Any]]` (Phase 4
  shape). Each dict carries Phase 7 metadata: `label`, `amount`,
  `section`, `category`, `is_reserve`, `is_revenue`, `read_only`.
  The fixture transcribed for Old Mill 2026 (plan 11-02) loads cleanly
  through `from_budget_history_record`.
* `ExtractedReserveStudyDocument` is a Pydantic model with `study_date:
  Optional[str]` and `rows: list[ExtractedReserveStudyRow]`. Each row
  has `line_item: str` (min_length=1), `useful_life: Optional[int]`,
  `remaining_life: Optional[int]`, `replacement_cost: Optional[float]`,
  `year_new: Optional[int]` — all match the adapter's expected attribute
  names. The float `replacement_cost` is the exact Pitfall 2 trigger
  the adapter exists for; `_to_decimal(str(float_value))` neutralizes it.
* `Property` ORM (SQLAlchemy `db/models.py:54`) has `id`, `name`,
  `units`, `fiscal_year_start_month`, `fiscal_year_end_month`, `tax_id`
  — all required attributes are present; no missing fields surfaced.

## Duck-typing adjustments for Phase 10 schema evolution (RESEARCH Risk #3)

The reserve-study adapter accepts both attribute and dict access via the
`_attr_or_key(obj, name, default)` helper. This means:

* If Phase 10 renames `replacement_cost` to a different field name, only
  the adapter changes — neither schemas nor formulas need touching.
* If Phase 10 returns a plain dict instead of a Pydantic model, the
  adapter still works.
* If Phase 10 adds a new row class with the same field names but
  different types (e.g., Pydantic v3), the adapter still works.

The HOA adapter uses the same helper for symmetry — if `Property` is
ever wrapped in a Pydantic response model with `from_attributes=True`,
the adapter handles both shapes without modification.

## Verification

| Check | Result |
|-------|--------|
| `pytest tests/test_disclosure_package_adapters.py -x` | PASS (8 tests, 0.05s) |
| `pytest tests/test_disclosure_package_preflight.py -x` | PASS (8 tests, 0.06s) |
| `pytest tests/test_disclosure_package_*.py` (full plan-11 suite) | PASS (55 tests, 0.10s) |
| Plan verify Task 1: `from_budget_history_record({...amount: 605.00...}).line_items[0].amount == Decimal('605.0')` | PASS |
| Plan verify Task 2: `validate_inputs(spec=SPECS['old_mill'], ...) == []` with valid inputs | PASS |
| `grep -E "from (app\.models|\.\.models)" backend/app/disclosure_package/*.py` | EMPTY (CONTEXT D-03 verified) |
| Field paths match UI-SPEC §9.3 row labels verbatim | PASS (asserted in tests 2, 3, 4, 5, 6) |
| `_to_decimal` is the single coercion chokepoint (T-11-04 mitigation) | PASS (only call site for money fields in adapters.py) |

## TDD Gate Compliance

This plan's tasks are `tdd="true"`. RED→GREEN cycle for each task:

* Task 1: tests authored first, run RED (1 fail under -x), then
  `adapters.py` written, all 8 pass. Single `feat(...)` commit
  `d5de78d` per plan 11-02's compressed-cycle precedent (RED→GREEN
  was sub-second; commit-splitting deferred per the SUMMARY-02
  recommendation).
* Task 2: same flow — tests authored first (RED), `preflight.py`
  written, all 8 pass (GREEN). Single `feat(...)` commit `8faea2e`.

Per gate-sequence note in plan-02 SUMMARY: future TDD plans where the
RED→GREEN cycle takes >2 min should split commits.

## Deviations from Plan

None. Plan 11-03 executed exactly as written:

* All 8 adapter tests + 8 preflight tests authored to the plan's
  `<behavior>` block.
* `adapters.py` and `preflight.py` content tracks the plan's `<action>`
  templates with two minor refinements documented above:
  1. Extracted the duck-typed read into a `_attr_or_key` helper
     (DRY — used 7+ times across the reserve-study + HOA adapters).
  2. Strengthened the reserve-row skip predicate to also drop rows
     missing `remaining_life` or `replacement_cost`. The plan's
     `<behavior>` Test 6 only mentions `useful_life`; the additional
     fields are mathematically required to construct a
     `ReserveStudyComponent`. Treated as a precision edit, not a
     deviation — no test or success criterion changed.

## Out-of-Scope Discoveries (NOT fixed)

Inherited from plan-02 SUMMARY (the same pre-existing breakage is still
present on this branch, unchanged by this plan):

* `tests/test_income_statement_parser.py::test_full_pipeline_esprit_park_structure`
  and `tests/test_sync_history_api.py::test_table_to_line_items_supports_headerless_income_statement_layout`
  fail on the merge base. Pre-existing; not caused by Phase 11-03 and
  not in scope to fix. Tracked in plan-02 SUMMARY.

## Self-Check: PASSED

- File `backend/app/disclosure_package/adapters.py` — FOUND
- File `backend/app/disclosure_package/preflight.py` — FOUND
- File `backend/tests/test_disclosure_package_adapters.py` — FOUND (8 tests)
- File `backend/tests/test_disclosure_package_preflight.py` — FOUND (8 tests)
- Commit `d5de78d` — FOUND in `git log`
- Commit `8faea2e` — FOUND in `git log`
- All 16 plan-03 tests pass; full disclosure_package suite (55 tests) green
- No `from app.models` import inside `disclosure_package/` (CONTEXT D-03 holds)
