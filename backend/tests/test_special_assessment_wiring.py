"""Non-engine wiring for pool-based special assessments
(add-variable-special-assessments): status inference, preflight, and the
compiler join that carries pool allocations onto the render entries.
"""
from decimal import Decimal

from app.assessment_engine.engine import SPECIAL_ASSESSMENT_POOL_KIND
from app.assessment_engine.schemas import PoolDefinition
from app.disclosure_package.assessment_schedule_matrix import (
    _pool_is_visible,
    _synthetic_special_assessment_lines,
)
from app.disclosure_package.compiler import (
    _apply_special_assessment_allocations,
    _normalize_special_assessment_for_render,
)
from app.disclosure_package.preflight import (
    check_special_assessments,
    infer_special_assessment_status,
)


# --- status inference ---------------------------------------------------------

def test_total_amount_entry_infers_scheduled():
    assert infer_special_assessment_status({"total_amount": 120000}) == "approved_scheduled"


def test_pool_key_entry_infers_scheduled():
    assert infer_special_assessment_status({"pool_key": "sa_roof"}) == "approved_scheduled"


def test_empty_entry_infers_none():
    assert infer_special_assessment_status({}) == "none"


# --- preflight ----------------------------------------------------------------

def test_total_only_scheduled_entry_passes_preflight():
    errs = check_special_assessments(
        entries=[{"pool_key": "sa_roof", "total_amount": 120000, "due_date": "03/01/2027"}]
    )
    assert errs == []


def test_scheduled_entry_missing_amount_and_pool_blocks():
    errs = check_special_assessments(
        entries=[{"status": "approved_scheduled", "due_date": "03/01/2027", "label": "X"}]
    )
    assert any(".amount_per_unit" in e.field_path for e in errs)


# --- compiler join ------------------------------------------------------------

class _Row:
    def __init__(self, recipient_label, amount):
        self.recipient_label = recipient_label
        self.amount = amount


class _Block:
    def __init__(self, pool_key, allocation_method, total, allocations):
        self.pool_key = pool_key
        self.allocation_method = allocation_method
        self.total = total
        self.allocations = allocations


class _Matrix:
    def __init__(self, blocks):
        self.special_assessment_blocks = blocks


def test_join_marks_variable_and_carries_table():
    entries = [_normalize_special_assessment_for_render({"pool_key": "sa_roof", "label": "Roof"})]
    matrix = _Matrix([
        _Block("sa_roof", "square_footage", 120000,
               [_Row("Unit 1", 84000), _Row("Unit 2", 36000)]),
    ])
    _apply_special_assessment_allocations(entries, matrix)
    e = entries[0]
    assert e["allocation_method"] == "square_footage"
    assert e["is_variable_allocation"] is True
    assert e["total_amount"] == 120000.0
    assert e["allocations"] == [
        {"recipient_label": "Unit 1", "amount": 84000.0},
        {"recipient_label": "Unit 2", "amount": 36000.0},
    ]


def test_join_equal_split_not_marked_variable():
    entries = [_normalize_special_assessment_for_render({"pool_key": "sa_fee", "label": "Fee"})]
    matrix = _Matrix([_Block("sa_fee", "equal", 1000, [_Row("Unit 1", 500), _Row("Unit 2", 500)])])
    _apply_special_assessment_allocations(entries, matrix)
    assert entries[0]["is_variable_allocation"] is False
    assert entries[0]["total_amount"] == 1000.0


def test_join_leaves_unlinked_entry_untouched():
    entries = [_normalize_special_assessment_for_render({"amount_per_unit": 50, "label": "Legacy"})]
    _apply_special_assessment_allocations(entries, _Matrix([]))
    assert entries[0]["allocation_method"] is None
    assert entries[0]["total_amount"] is None
    assert entries[0]["amount_per_unit"] == 50.0


# --- synthetic operator-total line injection ---------------------------------

def _special_pool_def(key="sa_roof"):
    return PoolDefinition(
        pool_id=1, pool_key=key, pool_name="SA", allocation_method="square_footage",
        recipient_scope="all_units", denominator_value=Decimal("1000"),
        pool_kind=SPECIAL_ASSESSMENT_POOL_KIND,
    )


def test_operator_total_becomes_synthetic_line_when_unmapped():
    lines, mappings = _synthetic_special_assessment_lines(
        pools=[_special_pool_def()],
        mappings=[],
        operator_totals={"sa_roof": Decimal("120000")},
        start_line_id=5,
    )
    assert len(lines) == 1 and len(mappings) == 1
    assert lines[0].amount == Decimal("120000")
    assert mappings[0].pool_key == "sa_roof"
    # The line and mapping share the disambiguating key so _aggregate_by_pool routes it.
    assert lines[0].normalized_label == mappings[0].budget_line_normalized_label


def test_no_synthetic_line_when_special_pool_already_mapped():
    from app.assessment_engine.schemas import BudgetLineMappingInput
    existing = [BudgetLineMappingInput(
        budget_line_normalized_label="roof", section="operating", category="operating",
        fund_type="operating", account_code=None, pool_key="sa_roof", active=True,
    )]
    lines, mappings = _synthetic_special_assessment_lines(
        pools=[_special_pool_def()], mappings=existing,
        operator_totals={"sa_roof": Decimal("120000")}, start_line_id=5,
    )
    assert lines == [] and mappings == []  # budget-derived total wins


def test_no_synthetic_line_without_operator_total():
    lines, mappings = _synthetic_special_assessment_lines(
        pools=[_special_pool_def()], mappings=[], operator_totals={}, start_line_id=5,
    )
    assert lines == [] and mappings == []


# --- column exclusion --------------------------------------------------------

def test_special_pool_hidden_from_regular_columns():
    special = _special_pool_def()
    regular = PoolDefinition(
        pool_id=2, pool_key="equal_costs", pool_name="Monthly",
        allocation_method="equal", recipient_scope="all_units",
    )
    assert _pool_is_visible(special) is False
    assert _pool_is_visible(regular) is True
