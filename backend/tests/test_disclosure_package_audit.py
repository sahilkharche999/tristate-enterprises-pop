"""Tests for backend/app/disclosure_package/audit.py (Phase 11 plan 02 Task 2).

CONTEXT D-05/D-07/D-15 + threat T-11-04: every formula must be recordable in
a per-render audit log. Each top-level call records exactly one FormulaCall
entry (RESEARCH OQ-8). Outside an audit context the decorator is a no-op so
formulas remain pure-functional and unit-testable.
"""
from __future__ import annotations

from decimal import Decimal


# ── Test 1: decorator is transparent (returns the original output) ───────────
def test_decorator_is_transparent_outside_context():
    from app.disclosure_package.audit import audit_formula

    @audit_formula(name="square", version=1)
    def square(*, n: Decimal) -> Decimal:
        return n * n

    assert square(n=Decimal("5")) == Decimal("25")
    # Metadata exposed for introspection / version checks.
    assert square.__audit_name__ == "square"
    assert square.__audit_version__ == 1


# ── Test 2: inside context, top-level call records exactly one entry ──────────
def test_top_level_call_records_one_entry():
    from app.disclosure_package.audit import audit_context, audit_formula

    @audit_formula(name="add", version=1)
    def add(*, a: Decimal, b: Decimal) -> Decimal:
        return a + b

    @audit_formula(name="add_then_double", version=1)
    def add_then_double(*, a: Decimal, b: Decimal) -> Decimal:
        # nested call to another decorated function; per OQ-8 only the top-level
        # invocation is recorded.
        return add(a=a, b=b) * Decimal("2")

    with audit_context({"a": "1", "b": "2"}) as log:
        result = add_then_double(a=Decimal("1"), b=Decimal("2"))

    assert result == Decimal("6")
    assert len(log.formula_calls) == 1
    assert log.formula_calls[0].formula_id == "add_then_double"
    assert log.formula_calls[0].version == 1


# ── Test 3: outside context, no recording, no exception ───────────────────────
def test_no_context_no_recording():
    from app.disclosure_package.audit import audit_formula

    @audit_formula(name="noop", version=1)
    def noop(*, x: int) -> int:
        return x + 1

    # Must not raise even though no audit_context is active.
    assert noop(x=41) == 42


# ── Test 4: AuditLog round-trips via JSON ─────────────────────────────────────
def test_audit_log_json_round_trip():
    from app.disclosure_package.audit import audit_context, audit_formula
    from app.disclosure_package.schemas import AuditLog

    @audit_formula(name="percent", version=2)
    def percent(*, num: Decimal, denom: Decimal) -> int:
        return int((num / denom * Decimal("100")).to_integral_value())

    with audit_context({"snapshot_marker": "x"}) as log:
        percent(num=Decimal("57"), denom=Decimal("100"))

    raw = log.model_dump_json()
    revived = AuditLog.model_validate_json(raw)
    assert revived.input_snapshot == {"snapshot_marker": "x"}
    assert len(revived.formula_calls) == 1
    assert revived.formula_calls[0].formula_id == "percent"
    assert revived.formula_calls[0].version == 2


# ── Test 5: call order is preserved ───────────────────────────────────────────
def test_call_order_preserved():
    from app.disclosure_package.audit import audit_context, audit_formula

    @audit_formula(name="step", version=1)
    def step(*, n: int) -> int:
        return n

    with audit_context({}) as log:
        step(n=1)
        step(n=2)
        step(n=3)

    assert [c.output for c in log.formula_calls] == [1, 2, 3]


# ── Test 6: Decimals serialize to strings; ints stay int ──────────────────────
def test_inputs_decimal_serialized_as_string_ints_stay_int():
    from app.disclosure_package.audit import audit_context, audit_formula

    @audit_formula(name="mix", version=1)
    def mix(*, money: Decimal, count: int) -> Decimal:
        return money * count

    with audit_context({}) as log:
        mix(money=Decimal("123.45"), count=2)

    call = log.formula_calls[0]
    assert call.inputs["money"] == "123.45"  # Decimal → str
    assert call.inputs["count"] == 2  # int unchanged
    assert call.output == "246.90"  # Decimal output also stringified


# ── Test 7: dict / Decimal outputs serialize stably ───────────────────────────
def test_output_decimal_dict_serialization_stable():
    from app.disclosure_package.audit import audit_context, audit_formula

    @audit_formula(name="bundle", version=1)
    def bundle(*, x: Decimal):
        return {"x": x, "doubled": x * 2}

    with audit_context({}) as log:
        bundle(x=Decimal("10.50"))

    call = log.formula_calls[0]
    assert call.output == {"x": "10.50", "doubled": "21.00"}
