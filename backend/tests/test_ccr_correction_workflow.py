from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.assessment_engine import PoolDefinition, RecipientReference
from app.assessment_engine import engine as assessment_engine
from app.dre_extraction import promotion
from app.disclosure_package import assessment_schedule_matrix
from app.services import ccr_approval_service, dre_review_service
from tests.support.missouri_allocation_fixture import (
    MISSOURI_UNITS,
    missouri_run_18_extraction_payload,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


def _pool(key: str, name: str | None = None) -> dict:
    return {
        "pool_key": key,
        "pool_name": name or key.replace("_", " ").title(),
        "annual_amount": "12000",
        "allocation_method": "equal",
        "recipient_scope": "all_units",
        "denominator_label": "units",
        "denominator_value": "10",
        "denominator_source": "dre_shown",
        "included_budget_lines": [],
        "excluded_budget_lines": [],
        "budget_line_derivation": "unknown",
        "residual_after_pool_keys": [],
        "residual_exclusions": [],
        "source_pages": [3],
        "confidence": 0.9,
    }


def _payload(*pools: dict) -> dict:
    return {
        "document_metadata": {"association_name": "Correction HOA"},
        "page_inventory": [],
        "assessment_setup": {
            "setup_type": "fixed_equal" if len(pools) == 1 else "multi_pool_combination",
            "display_mode": "",
            "summary": "",
            "requires_dre_for_future_years": True,
            "confidence": 0.9,
            "source_pages": [1],
        },
        "unit_structure": {
            "unit_count": 10,
            "group_count": 0,
            "groups": [],
            "units": [],
        },
        "allocation_pools": list(pools),
        "formulas": [],
        "reserve_setup": None,
        "validation_checks": [],
        "human_review_questions": [],
        "recommended_saved_setup": None,
    }


@pytest.fixture
def audit_db(tmp_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "audit.db")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA_PATH.read_text())
    connection.execute("INSERT INTO properties (name, units) VALUES ('Correction HOA', 10)")
    property_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    connection.execute(
        "INSERT INTO dre_documents (property_id, file_id, file_name, status, document_type) "
        "VALUES (?, 'ccr/test.pdf', 'test.pdf', 'active', 'ccr')",
        (property_id,),
    )
    document_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    connection.execute(
        "INSERT INTO dre_extraction_runs "
        "(dre_document_id, property_id, model_name, prompt_version, prompt_sha256, "
        "status, parsed_json, document_type) "
        "VALUES (?, ?, 'test', '1', 'hash', 'succeeded', ?, 'ccr')",
        (document_id, property_id, json.dumps(_payload(_pool("operating")))),
    )
    connection.commit()
    yield connection
    connection.close()


def _edit(operation: dict) -> SimpleNamespace:
    return SimpleNamespace(
        field_path="allocation_pools.$operation",
        new_value=json.dumps(operation),
    )


def test_list_review_edit_is_coerced_from_typed_json() -> None:
    extraction = promotion.parse_extraction_payload(json.dumps(_payload(_pool("operating"))))
    assert extraction is not None
    edit = SimpleNamespace(
        field_path="allocation_pools[0].included_budget_lines",
        new_value='["Insurance","Utilities"]',
    )

    resolved = promotion.apply_review_edits_to_extraction(extraction, [edit])

    assert resolved.allocation_pools[0].included_budget_lines == [
        "Insurance",
        "Utilities",
    ]


def test_structural_operations_replay_add_split_merge_remove_update_by_key() -> None:
    extraction = promotion.parse_extraction_payload(
        json.dumps(_payload(_pool("operating"), _pool("reserve")))
    )
    assert extraction is not None
    operations = [
        _edit(
            {
                "operation": "add",
                "base_version": 0,
                "category_key": "parking",
                "pool": _pool("parking"),
            }
        ),
        _edit(
            {
                "operation": "split",
                "base_version": 1,
                "category_key": "operating",
                "pools": [_pool("base"), _pool("exceptions")],
            }
        ),
        _edit(
            {
                "operation": "merge",
                "base_version": 2,
                "category_keys": ["base", "exceptions"],
                "pool": _pool("operating_corrected"),
            }
        ),
        _edit(
            {
                "operation": "update",
                "base_version": 3,
                "category_key": "reserve",
                "changes": {
                    "pool_name": "Replacement Reserve",
                    "source_pages": [7, 8],
                },
            }
        ),
        _edit(
            {
                "operation": "remove",
                "base_version": 4,
                "category_key": "parking",
            }
        ),
    ]

    resolved = promotion.apply_review_edits_to_extraction(extraction, operations)

    assert [pool.pool_key for pool in resolved.allocation_pools] == [
        "operating_corrected",
        "reserve",
    ]
    reserve = next(pool for pool in resolved.allocation_pools if pool.pool_key == "reserve")
    assert reserve.pool_name == "Replacement Reserve"
    assert reserve.source_pages == [7, 8]


def test_structural_update_targets_stable_key_after_order_changes() -> None:
    extraction = promotion.parse_extraction_payload(
        json.dumps(_payload(_pool("first"), _pool("target")))
    )
    assert extraction is not None
    edits = [
        _edit(
            {
                "operation": "remove",
                "base_version": 0,
                "category_key": "first",
            }
        ),
        _edit(
            {
                "operation": "update",
                "base_version": 1,
                "category_key": "target",
                "changes": {"pool_name": "Still The Target"},
            }
        ),
    ]

    resolved = promotion.apply_review_edits_to_extraction(extraction, edits)

    assert resolved.allocation_pools[0].pool_key == "target"
    assert resolved.allocation_pools[0].pool_name == "Still The Target"


def test_structural_operations_automatically_maintain_residual_relationships() -> None:
    residual = _pool("operating")
    residual["budget_line_derivation"] = "residual_default"
    residual["residual_after_pool_keys"] = ["reserve"]
    extraction = promotion.parse_extraction_payload(
        json.dumps(_payload(residual, _pool("reserve")))
    )
    assert extraction is not None
    edits = [
        _edit(
            {
                "operation": "add",
                "base_version": 0,
                "category_key": "parking",
                "pool": _pool("parking"),
            }
        ),
        _edit(
            {
                "operation": "split",
                "base_version": 1,
                "category_key": "parking",
                "pools": [_pool("garage"), _pool("surface")],
            }
        ),
        _edit(
            {
                "operation": "merge",
                "base_version": 2,
                "category_keys": ["reserve", "garage"],
                "pool": _pool("capital"),
            }
        ),
        _edit(
            {
                "operation": "remove",
                "base_version": 3,
                "category_key": "surface",
            }
        ),
    ]

    resolved = promotion.apply_review_edits_to_extraction(extraction, edits)

    operating = next(
        pool for pool in resolved.allocation_pools if pool.pool_key == "operating"
    )
    assert operating.residual_after_pool_keys == ["capital"]


def test_new_residual_category_automatically_excludes_peer_categories() -> None:
    extraction = promotion.parse_extraction_payload(
        json.dumps(_payload(_pool("reserve"), _pool("parking")))
    )
    assert extraction is not None
    residual = _pool("operating")
    residual["budget_line_derivation"] = "residual_default"

    resolved = promotion.apply_review_edits_to_extraction(
        extraction,
        [
            _edit(
                {
                    "operation": "add",
                    "base_version": 0,
                    "category_key": "operating",
                    "pool": residual,
                }
            )
        ],
    )

    operating = next(
        pool for pool in resolved.allocation_pools if pool.pool_key == "operating"
    )
    assert operating.residual_after_pool_keys == ["reserve", "parking"]


def test_custom_recipient_scope_preserves_participants_and_never_widens_unknown() -> None:
    custom = _pool("parking")
    custom["recipient_scope"] = "custom_unit_list"
    custom["participant_unit_numbers"] = ["101", "103"]
    extraction = promotion.parse_extraction_payload(json.dumps(_payload(custom)))
    assert extraction is not None

    resolved = promotion.apply_review_edits_to_extraction(
        extraction,
        [
            _edit(
                {
                    "operation": "update",
                    "base_version": 0,
                    "category_key": "parking",
                    "changes": {"pool_name": "Selected homes"},
                }
            )
        ],
    )

    assert resolved.allocation_pools[0].recipient_scope == "custom_unit_list"
    assert resolved.allocation_pools[0].participant_unit_numbers == ["101", "103"]
    with pytest.raises(promotion.InvalidStructuralOperation):
        promotion._coerce_recipient_scope("an unsupported scope")


def test_custom_recipient_scope_requires_at_least_one_home() -> None:
    custom = _pool("parking")
    custom["recipient_scope"] = "custom_unit_list"
    custom["participant_unit_numbers"] = []
    extraction = promotion.parse_extraction_payload(json.dumps(_payload(_pool("operating"))))
    assert extraction is not None

    with pytest.raises(promotion.InvalidStructuralOperation):
        promotion.apply_review_edits_to_extraction(
            extraction,
            [
                _edit(
                    {
                        "operation": "add",
                        "base_version": 0,
                        "category_key": "parking",
                        "pool": custom,
                    }
                )
            ],
        )


@pytest.mark.parametrize(
    "scope",
    ["residential_only", "commercial_only", "parking_users", "custom_unit_list"],
)
def test_structural_non_all_scope_requires_selected_home_numbers(
    scope: str,
) -> None:
    candidate = _pool("subset")
    candidate["recipient_scope"] = scope
    extraction = promotion.parse_extraction_payload(
        json.dumps(_payload(_pool("operating")))
    )
    assert extraction is not None

    with pytest.raises(promotion.InvalidStructuralOperation):
        promotion.apply_review_edits_to_extraction(
            extraction,
            [
                _edit(
                    {
                        "operation": "add",
                        "base_version": 0,
                        "category_key": "subset",
                        "pool": candidate,
                    }
                )
            ],
        )


def test_named_subset_selected_home_numbers_survive_structural_replay() -> None:
    candidate = _pool("residential")
    candidate["recipient_scope"] = "residential_only"
    candidate["selected_unit_numbers"] = ["101"]
    extraction = promotion.parse_extraction_payload(
        json.dumps(_payload(_pool("operating")))
    )
    assert extraction is not None

    resolved = promotion.apply_review_edits_to_extraction(
        extraction,
        [
            _edit(
                {
                    "operation": "add",
                    "base_version": 0,
                    "category_key": "residential",
                    "pool": candidate,
                }
            )
        ],
    )

    assert resolved.allocation_pools[-1].selected_unit_numbers == ["101"]


def test_legacy_participant_edits_stay_bidirectionally_canonical() -> None:
    legacy = _pool("parking")
    legacy.update(
        {
            "recipient_scope": "custom_unit_list",
            "participant_unit_numbers": ["101"],
        }
    )
    extraction = promotion.parse_extraction_payload(json.dumps(_payload(legacy)))
    assert extraction is not None
    assert extraction.allocation_pools[0].selected_unit_numbers == ["101"]

    scalar = SimpleNamespace(
        field_path="allocation_pools[0].participant_unit_numbers",
        new_value='["102"]',
    )
    scalar_resolved = promotion.apply_review_edits_to_extraction(
        extraction,
        [scalar],
    )
    assert scalar_resolved.allocation_pools[0].selected_unit_numbers == ["102"]
    assert scalar_resolved.allocation_pools[0].participant_unit_numbers == ["102"]

    structural = promotion.apply_review_edits_to_extraction(
        scalar_resolved,
        [
            _edit(
                {
                    "operation": "update",
                    "base_version": 0,
                    "category_key": "parking",
                    "changes": {"participant_unit_numbers": ["103"]},
                }
            )
        ],
    )
    assert structural.allocation_pools[0].selected_unit_numbers == ["103"]
    assert structural.allocation_pools[0].participant_unit_numbers == ["103"]


def test_named_subset_promotion_derives_evidenced_participants_and_uses_only_them(
    audit_db: sqlite3.Connection,
) -> None:
    property_id = audit_db.execute("SELECT id FROM properties").fetchone()[0]
    audit_db.execute(
        "INSERT INTO assessment_setups "
        "(property_id, setup_type, display_mode, status) "
        "VALUES (?, 'per_unit', 'per_unit', 'draft')",
        (property_id,),
    )
    setup_id = audit_db.execute("SELECT last_insert_rowid()").fetchone()[0]
    fixed = _pool("residential_fixed")
    fixed.update(
        {
            "allocation_method": "specified_value",
            "annual_amount": "1200",
            "recipient_scope": "residential_only",
        }
    )
    payload = _payload(fixed)
    payload["unit_structure"]["unit_count"] = 2
    payload["unit_structure"]["units"] = [
        {"unit_number": "101", "category": "residential"},
        {"unit_number": "201", "category": "commercial"},
    ]
    extraction = promotion.parse_extraction_payload(json.dumps(payload))
    assert extraction is not None
    merged = ccr_approval_service.merge_operator_factors(
        extraction,
        {
            "101": {"fixed_amounts": {"residential_fixed": "1200"}},
            "201": {},
        },
    )

    counts = promotion.populate_setup_children(
        setup_id=setup_id,
        setup_type="per_unit",
        extraction=merged,
        edited_entity_keys=frozenset({"unit:101"}),
        connection=audit_db,
    )

    promoted_scope = audit_db.execute(
        "SELECT recipient_scope FROM allocation_pools "
        "WHERE assessment_setup_id = ? AND pool_key = 'residential_fixed'",
        (setup_id,),
    ).fetchone()[0]
    rows = audit_db.execute(
        "SELECT u.unit_number, a.specified_monthly_amount "
        "FROM assessment_unit_pool_allocations a "
        "JOIN assessment_units u ON u.id = a.assessment_unit_id "
        "WHERE a.assessment_setup_id = ?",
        (setup_id,),
    ).fetchall()
    assert promoted_scope == "custom_unit_list"
    assert [(unit, Decimal(str(amount))) for unit, amount in rows] == [
        ("101", Decimal("100"))
    ]
    assert "specified_value_placeholders" not in counts


def test_named_subset_without_selection_or_unit_evidence_fails_closed(
    audit_db: sqlite3.Connection,
) -> None:
    property_id = audit_db.execute("SELECT id FROM properties").fetchone()[0]
    audit_db.execute(
        "INSERT INTO assessment_setups "
        "(property_id, setup_type, display_mode, status) "
        "VALUES (?, 'per_unit', 'per_unit', 'draft')",
        (property_id,),
    )
    setup_id = audit_db.execute("SELECT last_insert_rowid()").fetchone()[0]
    subset = _pool("parking")
    subset["recipient_scope"] = "parking_users"
    payload = _payload(subset)
    payload["unit_structure"]["unit_count"] = 1
    payload["unit_structure"]["units"] = [{"unit_number": "101"}]
    extraction = promotion.parse_extraction_payload(json.dumps(payload))
    assert extraction is not None

    with pytest.raises(promotion.InvalidStructuralOperation):
        promotion.populate_setup_children(
            setup_id=setup_id,
            setup_type="per_unit",
            extraction=extraction,
            connection=audit_db,
        )


@pytest.mark.parametrize("annual_amount", [None, "0", "-1", "not-a-number"])
def test_structural_operation_rejects_invalid_known_amount(
    annual_amount: str | None,
) -> None:
    candidate = _pool("reserve")
    candidate["amount_availability"] = "known"
    candidate["annual_amount"] = annual_amount
    extraction = promotion.parse_extraction_payload(json.dumps(_payload(_pool("operating"))))
    assert extraction is not None

    with pytest.raises((promotion.InvalidStructuralOperation, ValueError)):
        promotion.apply_review_edits_to_extraction(
            extraction,
            [
                _edit(
                    {
                        "operation": "add",
                        "base_version": 0,
                        "category_key": "reserve",
                        "pool": candidate,
                    }
                )
            ],
        )


def test_operator_fixed_amounts_merge_as_per_pool_dollar_factors() -> None:
    fixed = _pool("fixed")
    fixed["allocation_method"] = "specified_value"
    fixed["annual_amount"] = "3600"
    payload = _payload(fixed)
    payload["unit_structure"]["unit_count"] = 2
    extraction = promotion.parse_extraction_payload(
        json.dumps(payload)
    )
    assert extraction is not None

    merged = ccr_approval_service.merge_operator_factors(
        extraction,
        {
            "101": {"fixed_amounts": {"fixed": "1200"}},
            "102": {"fixed_amounts": {"fixed": "2400"}},
        },
    )

    assert [
        (
            unit.unit_number,
            unit.pool_factors[0].pool_key,
            unit.pool_factors[0].factor_type,
            unit.pool_factors[0].factor_value,
        )
        for unit in merged.unit_structure.units
    ] == [
        ("101", "fixed", "dollar_amount", Decimal("1200")),
        ("102", "fixed", "dollar_amount", Decimal("2400")),
    ]


def test_operator_custom_factors_merge_as_per_pool_weights() -> None:
    custom = _pool("custom")
    custom["allocation_method"] = "custom_factor"
    custom["amount_availability"] = "external_schedule"
    custom["annual_amount"] = None
    payload = _payload(custom)
    payload["unit_structure"]["unit_count"] = 2
    extraction = promotion.parse_extraction_payload(json.dumps(payload))
    assert extraction is not None

    merged = ccr_approval_service.merge_operator_factors(
        extraction,
        {
            "101": {"custom_factors": {"custom": "2"}},
            "102": {"custom_factors": {"custom": "3"}},
        },
    )

    assert [
        (
            unit.unit_number,
            unit.pool_factors[0].pool_key,
            unit.pool_factors[0].factor_type,
            unit.pool_factors[0].factor_value,
        )
        for unit in merged.unit_structure.units
    ] == [
        ("101", "custom", "raw_factor", Decimal("2")),
        ("102", "custom", "raw_factor", Decimal("3")),
    ]
    assert promotion.check_missing_unit_factors(merged) == []


def test_equal_subset_roster_persists_preserves_values_and_promotes_units(
    audit_db: sqlite3.Connection,
) -> None:
    subset = _pool("parking")
    subset.update(
        {
            "recipient_scope": "custom_unit_list",
            "selected_unit_numbers": ["A", "B"],
        }
    )
    payload = _payload(subset)
    payload["unit_structure"].update(
        {
            "unit_count": 2,
            "units": [
                {
                    "unit_number": "A",
                    "square_feet": "1000",
                    "category": "residential",
                }
            ],
        }
    )
    run_id = audit_db.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]
    property_id = audit_db.execute("SELECT id FROM properties").fetchone()[0]
    audit_db.execute(
        "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
        (json.dumps(payload), run_id),
    )
    ccr_approval_service.save_operator_unit_factors(
        extraction_run_id=run_id,
        property_id=property_id,
        factors=[
            ccr_approval_service.CCRUnitFactor(
                unit_number="A",
                square_feet=Decimal("1200"),
            ),
            ccr_approval_service.CCRUnitFactor(unit_number="B"),
        ],
        connection=audit_db,
    )
    count = ccr_approval_service.save_operator_unit_factors(
        extraction_run_id=run_id,
        property_id=property_id,
        factors=[
            ccr_approval_service.CCRUnitFactor(
                unit_number="A",
                square_feet=None,
                ownership_percent=None,
            ),
            ccr_approval_service.CCRUnitFactor(
                unit_number="B",
                square_feet=None,
                ownership_percent=None,
            ),
        ],
        connection=audit_db,
    )
    assert count == 2
    assert ccr_approval_service.get_operator_unit_factors(
        extraction_run_id=run_id,
        connection=audit_db,
    ) == {"A": {"square_feet": "1200"}, "B": {}}

    extraction = promotion.parse_extraction_payload(json.dumps(payload))
    assert extraction is not None
    merged = ccr_approval_service.merge_operator_factors(
        extraction,
        {"A": {"square_feet": "1200"}, "B": {}},
    )
    assert [
        (unit.unit_number, unit.square_feet)
        for unit in merged.unit_structure.units
    ] == [("A", Decimal("1200")), ("B", None)]

    audit_db.execute(
        "INSERT INTO assessment_setups "
        "(property_id, setup_type, display_mode, status) "
        "VALUES (?, 'per_unit', 'per_unit', 'draft')",
        (property_id,),
    )
    setup_id = audit_db.execute("SELECT last_insert_rowid()").fetchone()[0]
    counts = promotion.populate_setup_children(
        setup_id=setup_id,
        setup_type="per_unit",
        extraction=merged,
        connection=audit_db,
    )

    assert counts["units"] == 2
    assert audit_db.execute(
        "SELECT unit_number, square_feet FROM assessment_units "
        "WHERE assessment_setup_id = ? ORDER BY id",
        (setup_id,),
    ).fetchall() == [("A", 1200), ("B", None)]
    assert audit_db.execute(
        "SELECT recipient_scope FROM allocation_pools "
        "WHERE assessment_setup_id = ? AND pool_key = 'parking'",
        (setup_id,),
    ).fetchone() == ("custom_unit_list",)


def test_partial_operator_roster_cannot_shrink_known_unit_count(
    audit_db: sqlite3.Connection,
) -> None:
    payload = _payload(_pool("operating"))
    payload["unit_structure"].update(
        {
            "unit_count": 3,
            "units": [{"unit_number": "A", "square_feet": "1000"}],
        }
    )
    run_id = audit_db.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]
    property_id = audit_db.execute("SELECT id FROM properties").fetchone()[0]
    audit_db.execute(
        "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
        (json.dumps(payload), run_id),
    )
    complete = [
        ccr_approval_service.CCRUnitFactor(unit_number=unit_number)
        for unit_number in ("A", "B", "C")
    ]
    ccr_approval_service.save_operator_unit_factors(
        extraction_run_id=run_id,
        property_id=property_id,
        factors=complete,
        connection=audit_db,
    )

    with pytest.raises(ccr_approval_service.IncompleteOperatorUnitRoster):
        ccr_approval_service.save_operator_unit_factors(
            extraction_run_id=run_id,
            property_id=property_id,
            factors=complete[:2],
            connection=audit_db,
        )
    with pytest.raises(ccr_approval_service.IncompleteOperatorUnitRoster):
        ccr_approval_service.save_operator_unit_factors(
            extraction_run_id=run_id,
            property_id=property_id,
            factors=[
                ccr_approval_service.CCRUnitFactor(unit_number="A"),
                ccr_approval_service.CCRUnitFactor(unit_number=" A "),
                ccr_approval_service.CCRUnitFactor(unit_number="C"),
            ],
            connection=audit_db,
        )
    assert ccr_approval_service.get_operator_unit_factors(
        extraction_run_id=run_id,
        connection=audit_db,
    ) == {"A": {}, "B": {}, "C": {}}

    extraction = promotion.parse_extraction_payload(json.dumps(payload))
    assert extraction is not None
    with pytest.raises(ccr_approval_service.IncompleteOperatorUnitRoster):
        ccr_approval_service.merge_operator_factors(
            extraction,
            {"A": {}, "B": {}},
        )
    assert extraction.unit_structure.unit_count == 3
    assert len(extraction.unit_structure.units) == 1


def test_complete_operator_roster_rename_replaces_without_phantom_units() -> None:
    payload = _payload(_pool("operating"))
    payload["unit_structure"].update(
        {
            "unit_count": 2,
            "units": [
                {"unit_number": "A", "square_feet": "1000"},
                {"unit_number": "B", "square_feet": "2000"},
            ],
        }
    )
    extraction = promotion.parse_extraction_payload(json.dumps(payload))
    assert extraction is not None

    merged = ccr_approval_service.merge_operator_factors(
        extraction,
        {"A": {}, "C": {}},
    )

    assert merged.unit_structure.unit_count == 2
    assert [
        (unit.unit_number, unit.square_feet)
        for unit in merged.unit_structure.units
    ] == [("A", Decimal("1000")), ("C", None)]


@pytest.mark.parametrize(
    ("method", "units"),
    [
        (
            "square_footage",
            [
                {"unit_number": "A", "square_feet": "1000"},
                {"unit_number": "B", "square_feet": None},
                {"unit_number": "C", "square_feet": None},
            ],
        ),
        (
            "ownership_percentage",
            [
                {"unit_number": "A", "ownership_percent": "50"},
                {"unit_number": "B", "ownership_percent": "0"},
                {"unit_number": "C", "ownership_percent": None},
            ],
        ),
        (
            "custom_factor",
            [
                {
                    "unit_number": "A",
                    "pool_factors": [
                        {
                            "pool_key": "subset",
                            "factor_value": "1",
                            "factor_type": "percent",
                        }
                    ],
                },
                {"unit_number": "B", "pool_factors": []},
                {"unit_number": "C", "pool_factors": []},
            ],
        ),
    ],
)
def test_missing_factor_check_requires_positive_values_for_every_participant(
    method: str,
    units: list[dict],
) -> None:
    subset = _pool("subset")
    subset.update(
        {
            "allocation_method": method,
            "recipient_scope": "custom_unit_list",
            "selected_unit_numbers": ["A", "B"],
        }
    )
    payload = _payload(subset)
    payload["unit_structure"].update({"unit_count": 3, "units": units})
    extraction = promotion.parse_extraction_payload(json.dumps(payload))
    assert extraction is not None

    assert promotion.check_missing_unit_factors(extraction) == ["subset"]

    fixed_units = list(units)
    if method == "square_footage":
        fixed_units[1] = {**fixed_units[1], "square_feet": "900"}
    elif method == "ownership_percentage":
        fixed_units[1] = {**fixed_units[1], "ownership_percent": "50"}
    else:
        fixed_units[1] = {
            **fixed_units[1],
            "pool_factors": [
                {
                    "pool_key": "subset",
                    "factor_value": "2",
                    "factor_type": "percent",
                }
            ],
        }
    complete_payload = {**payload}
    complete_payload["unit_structure"] = {
        **payload["unit_structure"],
        "units": fixed_units,
    }
    complete = promotion.parse_extraction_payload(json.dumps(complete_payload))
    assert complete is not None
    assert promotion.check_missing_unit_factors(complete) == []


@pytest.mark.parametrize("invalid_value", [None, Decimal("0"), Decimal("-1"), "bad"])
def test_custom_factor_invalid_values_block_only_applicable_participants(
    invalid_value: object,
) -> None:
    subset = _pool("subset")
    subset.update(
        {
            "allocation_method": "custom_factor",
            "recipient_scope": "custom_unit_list",
            "selected_unit_numbers": ["A", "B"],
        }
    )
    payload = _payload(subset)
    payload["unit_structure"].update(
        {
            "unit_count": 3,
            "units": [
                {
                    "unit_number": unit_number,
                    "pool_factors": [
                        {
                            "pool_key": "subset",
                            "factor_value": "1",
                            "factor_type": "percent",
                        }
                    ],
                }
                for unit_number in ("A", "B", "C")
            ],
        }
    )
    extraction = promotion.parse_extraction_payload(json.dumps(payload))
    assert extraction is not None
    participant = extraction.unit_structure.units[1]
    participant.pool_factors[0] = participant.pool_factors[0].model_construct(
        pool_key="subset",
        factor_value=invalid_value,
        factor_type="percent",
    )

    assert promotion.check_missing_unit_factors(extraction) == ["subset"]

    participant.pool_factors[0] = participant.pool_factors[0].model_copy(
        update={"factor_value": Decimal("2")}
    )
    nonparticipant = extraction.unit_structure.units[2]
    nonparticipant.pool_factors[0] = nonparticipant.pool_factors[0].model_construct(
        pool_key="subset",
        factor_value=invalid_value,
        factor_type="percent",
    )
    assert promotion.check_missing_unit_factors(extraction) == []


def test_operator_fixed_amounts_promote_without_equal_placeholders(
    audit_db: sqlite3.Connection,
) -> None:
    property_id = audit_db.execute("SELECT id FROM properties").fetchone()[0]
    audit_db.execute(
        "INSERT INTO assessment_setups "
        "(property_id, setup_type, display_mode, status) "
        "VALUES (?, 'per_unit', 'per_unit', 'draft')",
        (property_id,),
    )
    setup_id = audit_db.execute("SELECT last_insert_rowid()").fetchone()[0]
    fixed = _pool("fixed")
    fixed["allocation_method"] = "specified_value"
    fixed["annual_amount"] = "3600"
    payload = _payload(fixed)
    payload["unit_structure"]["unit_count"] = 2
    extraction = promotion.parse_extraction_payload(json.dumps(payload))
    assert extraction is not None
    merged = ccr_approval_service.merge_operator_factors(
        extraction,
        {
            "101": {"fixed_amounts": {"fixed": "1200"}},
            "102": {"fixed_amounts": {"fixed": "2400"}},
        },
    )

    counts = promotion.populate_setup_children(
        setup_id=setup_id,
        setup_type="per_unit",
        extraction=merged,
        edited_entity_keys=frozenset({"unit:101", "unit:102"}),
        connection=audit_db,
    )

    rows = audit_db.execute(
        "SELECT u.unit_number, a.specified_monthly_amount, a.source "
        "FROM assessment_unit_pool_allocations a "
        "JOIN assessment_units u ON u.id = a.assessment_unit_id "
        "WHERE a.assessment_setup_id = ? ORDER BY u.unit_number",
        (setup_id,),
    ).fetchall()
    assert [
        (unit, Decimal(str(amount)), source)
        for unit, amount, source in rows
    ] == [
        ("101", Decimal("100"), "manual"),
        ("102", Decimal("200"), "manual"),
    ]
    assert "specified_value_placeholders" not in counts


def test_custom_fixed_amounts_promote_only_selected_homes_without_placeholder(
    audit_db: sqlite3.Connection,
) -> None:
    property_id = audit_db.execute("SELECT id FROM properties").fetchone()[0]
    audit_db.execute(
        "INSERT INTO assessment_setups "
        "(property_id, setup_type, display_mode, status) "
        "VALUES (?, 'per_unit', 'per_unit', 'draft')",
        (property_id,),
    )
    setup_id = audit_db.execute("SELECT last_insert_rowid()").fetchone()[0]
    fixed = _pool("fixed")
    fixed.update(
        {
            "allocation_method": "specified_value",
            "annual_amount": "1200",
            "recipient_scope": "custom_unit_list",
            "participant_unit_numbers": ["101"],
        }
    )
    payload = _payload(fixed)
    payload["unit_structure"]["unit_count"] = 2
    extraction = promotion.parse_extraction_payload(json.dumps(payload))
    assert extraction is not None
    merged = ccr_approval_service.merge_operator_factors(
        extraction,
        {
            "101": {"fixed_amounts": {"fixed": "1200"}},
            "102": {},
        },
    )

    counts = promotion.populate_setup_children(
        setup_id=setup_id,
        setup_type="per_unit",
        extraction=merged,
        edited_entity_keys=frozenset({"unit:101"}),
        connection=audit_db,
    )

    rows = audit_db.execute(
        "SELECT u.unit_number, a.specified_monthly_amount "
        "FROM assessment_unit_pool_allocations a "
        "JOIN assessment_units u ON u.id = a.assessment_unit_id "
        "WHERE a.assessment_setup_id = ?",
        (setup_id,),
    ).fetchall()
    assert [(unit, Decimal(str(amount))) for unit, amount in rows] == [
        ("101", Decimal("100"))
    ]
    assert "specified_value_placeholders" not in counts


def test_factor_api_accepts_zero_fixed_home_amount(
    client,
    db_session,
) -> None:
    payload = _payload(_pool("operating"))
    payload["unit_structure"].update(
        {"unit_count": 1, "units": [{"unit_number": "101"}]}
    )
    property_id, run_id = _seed_api_run(db_session, payload)

    response = client.post(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/factors",
        json={
            "factors": [
                {
                    "unit_number": "101",
                    "fixed_amounts": {"parking": "0"},
                }
            ]
        },
    )

    assert response.status_code == 200, response.text


def test_factor_api_rejects_nonpositive_fixed_home_amount(
    client,
    db_session,
) -> None:
    property_id, run_id = _seed_api_run(db_session, _payload(_pool("operating")))

    response = client.post(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/factors",
        json={
            "factors": [
                {
                    "unit_number": "101",
                    "fixed_amounts": {"capital": "-1"},
                }
            ]
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize("invalid_value", [0, -1])
def test_factor_api_rejects_nonpositive_custom_factor(
    client,
    db_session,
    invalid_value: int,
) -> None:
    property_id, run_id = _seed_api_run(db_session, _payload(_pool("operating")))

    response = client.post(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/factors",
        json={
            "factors": [
                {
                    "unit_number": "101",
                    "custom_factors": {"capital": invalid_value},
                }
            ]
        },
    )

    assert response.status_code == 422


def test_factor_api_rejects_partial_replacement_roster(
    client,
    db_session,
) -> None:
    payload = _payload(_pool("operating"))
    payload["unit_structure"].update(
        {
            "unit_count": 3,
            "units": [{"unit_number": "A"}],
        }
    )
    property_id, run_id = _seed_api_run(db_session, payload)

    response = client.post(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/factors",
        json={
            "factors": [
                {
                    "unit_number": "A",
                    "square_feet": None,
                    "ownership_percent": None,
                },
                {
                    "unit_number": "B",
                    "square_feet": None,
                    "ownership_percent": None,
                },
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["message"] == (
        "Enter all 3 distinct homes before saving this roster."
    )


def test_custom_participants_map_to_engine_recipient_ids() -> None:
    payload = {
        "allocation_pools": [
            {
                "pool_key": "parking",
                "recipient_scope": "parking_users",
                "selected_unit_numbers": ["101", "103"],
            }
        ]
    }

    assert assessment_schedule_matrix._pool_custom_recipient_ids_from_payload(
        payload=payload,
        unit_id_by_number={"101": 11, "102": 12, "103": 13},
    ) == {"parking": [11, 13]}


def test_scalar_edit_after_structural_operation_keeps_append_order() -> None:
    extraction = promotion.parse_extraction_payload(
        json.dumps(_payload(_pool("operating")))
    )
    assert extraction is not None
    edits = [
        _edit(
            {
                "operation": "add",
                "base_version": 0,
                "category_key": "reserve",
                "pool": _pool("reserve"),
            }
        ),
        SimpleNamespace(
            field_path="allocation_pools[1].included_budget_lines",
            new_value='["Reserve contribution"]',
        ),
    ]

    resolved = promotion.apply_review_edits_to_extraction(extraction, edits)

    reserve = next(pool for pool in resolved.allocation_pools if pool.pool_key == "reserve")
    assert reserve.included_budget_lines == ["Reserve contribution"]


def test_structural_operations_mark_stable_pool_keys_as_operator_edited() -> None:
    extraction = promotion.parse_extraction_payload(
        json.dumps(_payload(_pool("operating")))
    )
    assert extraction is not None
    edits = [
        _edit(
            {
                "operation": "split",
                "base_version": 0,
                "category_key": "operating",
                "pools": [_pool("base"), _pool("exceptions")],
            }
        )
    ]
    resolved = promotion.apply_review_edits_to_extraction(extraction, edits)

    touched = promotion.entity_keys_touched_by_edits(
        resolved,
        edits,
    )

    assert touched == frozenset(
        {"pool:operating", "pool:base", "pool:exceptions"}
    )


def test_record_structural_operation_is_append_only_and_rejects_stale_base(
    audit_db: sqlite3.Connection,
) -> None:
    run_id = audit_db.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]
    record = getattr(dre_review_service, "record_structural_operation")
    stale_error = getattr(dre_review_service, "StaleStructuralOperation")

    record(
        dre_extraction_run_id=run_id,
        operation={
            "operation": "update",
            "base_version": 0,
            "category_key": "operating",
            "changes": {"pool_name": "Corrected Operating"},
        },
        reason="Correct the category name",
        edited_by="bob@example.com",
        connection=audit_db,
    )
    with pytest.raises(stale_error):
        record(
            dre_extraction_run_id=run_id,
            operation={
                "operation": "remove",
                "base_version": 0,
                "category_key": "operating",
            },
            reason="Remove the duplicate category",
            edited_by="bob@example.com",
            connection=audit_db,
        )

    rows = audit_db.execute(
        "SELECT field_path, new_value FROM dre_review_edits ORDER BY id"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "allocation_pools.$operation"
    assert json.loads(rows[0][1])["operation"] == "update"


@pytest.mark.parametrize(
    "candidate",
    [
        {
            "operation": "remove",
            "base_version": 1,
            "category_key": "missing",
        },
        {
            "operation": "update",
            "base_version": 1,
            "category_key": "operating",
            "changes": {"not_a_pool_field": "bad"},
        },
    ],
)
def test_invalid_structural_candidate_is_not_persisted_and_log_stays_replayable(
    audit_db: sqlite3.Connection,
    candidate: dict,
) -> None:
    run_id = audit_db.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]
    dre_review_service.record_structural_operation(
        dre_extraction_run_id=run_id,
        operation={
            "operation": "update",
            "base_version": 0,
            "category_key": "operating",
            "changes": {"pool_name": "Valid Existing Correction"},
        },
        reason="Keep a valid replay baseline",
        connection=audit_db,
    )

    with pytest.raises(promotion.InvalidStructuralOperation):
        dre_review_service.record_structural_operation(
            dre_extraction_run_id=run_id,
            operation=candidate,
            reason="Exercise candidate validation",
            connection=audit_db,
        )

    edits = dre_review_service.list_review_edits(
        dre_extraction_run_id=run_id,
        connection=audit_db,
    )
    extraction = promotion.parse_extraction_payload(
        audit_db.execute(
            "SELECT parsed_json FROM dre_extraction_runs WHERE id = ?",
            (run_id,),
        ).fetchone()[0]
    )
    assert extraction is not None
    replayed = promotion.apply_review_edits_to_extraction(extraction, edits)
    assert replayed.allocation_pools[0].pool_name == "Valid Existing Correction"
    assert len(edits) == 1


def test_structural_operation_does_not_commit_caller_transaction(
    audit_db: sqlite3.Connection,
) -> None:
    run_id = audit_db.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]
    audit_db.execute("BEGIN IMMEDIATE")

    dre_review_service.record_structural_operation(
        dre_extraction_run_id=run_id,
        operation={
            "operation": "update",
            "base_version": 0,
            "category_key": "operating",
            "changes": {"pool_name": "Uncommitted Correction"},
        },
        reason="Verify caller transaction ownership",
        connection=audit_db,
    )

    assert audit_db.in_transaction is True
    audit_db.rollback()
    assert audit_db.execute(
        "SELECT COUNT(*) FROM dre_review_edits WHERE dre_extraction_run_id = ?",
        (run_id,),
    ).fetchone()[0] == 0


def test_concurrent_structural_base_version_allows_only_one_writer(
    audit_db: sqlite3.Connection,
) -> None:
    run_id = audit_db.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]
    db_path = audit_db.execute("PRAGMA database_list").fetchone()[2]
    audit_db.close()

    def _record(pool_name: str) -> str:
        connection = sqlite3.connect(db_path, timeout=5)
        try:
            dre_review_service.record_structural_operation(
                dre_extraction_run_id=run_id,
                operation={
                    "operation": "update",
                    "base_version": 0,
                    "category_key": "operating",
                    "changes": {"pool_name": pool_name},
                },
                reason="Concurrent operator correction",
                connection=connection,
            )
            return "written"
        except promotion.StaleStructuralOperation:
            return "stale"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(_record, ["Correction A", "Correction B"]))

    verification = sqlite3.connect(db_path)
    try:
        assert sorted(outcomes) == ["stale", "written"]
        assert verification.execute(
            "SELECT COUNT(*) FROM dre_review_edits WHERE dre_extraction_run_id = ?",
            (run_id,),
        ).fetchone()[0] == 1
    finally:
        verification.close()


def test_review_api_rejects_stale_structural_operation(client, db_session) -> None:
    property_id, run_id = _seed_api_run(db_session, _payload(_pool("operating")))
    endpoint = f"/hoa/{property_id}/dre/extraction-runs/{run_id}/edits"
    first = client.post(
        endpoint,
        json={
            "field_path": "allocation_pools.$operation",
            "new_value": {
                "operation": "update",
                "base_version": 0,
                "category_key": "operating",
                "changes": {"pool_name": "First Correction"},
            },
            "reason": "Correct the category name.",
        },
    )
    stale = client.post(
        endpoint,
        json={
            "field_path": "allocation_pools.$operation",
            "new_value": {
                "operation": "remove",
                "base_version": 0,
                "category_key": "operating",
            },
            "reason": "Remove the duplicate category.",
        },
    )

    assert first.status_code == 200, first.text
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "STALE_OPERATION_VERSION"
    raw = db_session.connection().connection
    assert raw.execute(
        "SELECT COUNT(*) FROM dre_review_edits WHERE dre_extraction_run_id = ?",
        (run_id,),
    ).fetchone()[0] == 1


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_review_api_requires_structural_operation_reason(
    client,
    db_session,
    reason: str | None,
) -> None:
    property_id, run_id = _seed_api_run(db_session, _payload(_pool("operating")))
    response = client.post(
        f"/hoa/{property_id}/dre/extraction-runs/{run_id}/edits",
        json={
            "field_path": "allocation_pools.$operation",
            "new_value": {
                "operation": "update",
                "base_version": 0,
                "category_key": "operating",
                "changes": {"pool_name": "Corrected"},
            },
            "reason": reason,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "STRUCTURAL_REASON_REQUIRED"
    assert "explain" in response.json()["detail"]["message"].lower()


def _seed_api_run(db_session, payload: dict) -> tuple[int, int]:
    raw = db_session.connection().connection
    raw.execute("INSERT INTO properties (name, units) VALUES ('Correction API HOA', 10)")
    property_id = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
    raw.execute(
        "INSERT INTO dre_documents (property_id, file_id, file_name, status, document_type) "
        "VALUES (?, 'ccr/api.pdf', 'api.pdf', 'active', 'ccr')",
        (property_id,),
    )
    document_id = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
    raw.execute(
        "INSERT INTO dre_extraction_runs "
        "(dre_document_id, property_id, model_name, prompt_version, prompt_sha256, "
        "status, parsed_json, document_type) "
        "VALUES (?, ?, 'test', '1', 'hash', 'succeeded', ?, 'ccr')",
        (document_id, property_id, json.dumps(payload)),
    )
    run_id = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
    raw.commit()
    return property_id, run_id


def _mutation_snapshot(raw, property_id: int, run_id: int) -> tuple:
    return (
        raw.execute(
            "SELECT parsed_json, review_status, promoted_setup_id, promoted_at "
            "FROM dre_extraction_runs WHERE id = ?",
            (run_id,),
        ).fetchone(),
        raw.execute(
            "SELECT COUNT(*) FROM assessment_setups WHERE property_id = ?",
            (property_id,),
        ).fetchone()[0],
        raw.execute(
            "SELECT default_assessment_setup_id FROM properties WHERE id = ?",
            (property_id,),
        ).fetchone()[0],
        raw.total_changes,
    )


def test_preview_is_non_mutating_and_matches_approval(client, db_session) -> None:
    property_id, run_id = _seed_api_run(db_session, _payload(_pool("operating")))
    raw = db_session.connection().connection
    record = getattr(dre_review_service, "record_structural_operation")
    record(
        dre_extraction_run_id=run_id,
        operation={
            "operation": "update",
            "base_version": 0,
            "category_key": "operating",
            "changes": {"pool_name": "Bob Corrected Operating"},
        },
        reason="Apply the reviewed category name",
        edited_by="bob@example.com",
        connection=raw,
    )
    before = _mutation_snapshot(raw, property_id, run_id)

    preview = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": "fixed"},
    )

    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["approval_blocked"] is False
    assert body["resolved_extraction"]["allocation_pools"][0]["pool_name"] == (
        "Bob Corrected Operating"
    )
    assert _mutation_snapshot(raw, property_id, run_id) == before

    approved = client.post(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/approve",
        json={"setup_type": "fixed"},
    )
    assert approved.status_code == 200, approved.text
    promoted_name = raw.execute(
        "SELECT pool_name FROM allocation_pools WHERE assessment_setup_id = ? "
        "AND pool_key = 'operating'",
        (approved.json()["promoted_setup_id"],),
    ).fetchone()[0]
    assert promoted_name == body["resolved_extraction"]["allocation_pools"][0]["pool_name"]


@pytest.mark.parametrize("raced_input", ["review", "factors"])
def test_approval_rejects_inputs_changed_after_resolution(
    audit_db: sqlite3.Connection,
    monkeypatch,
    raced_input: str,
) -> None:
    property_id = audit_db.execute("SELECT id FROM properties").fetchone()[0]
    run_id = audit_db.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]
    db_path = audit_db.execute("PRAGMA database_list").fetchone()[2]
    original_resolver = ccr_approval_service.resolve_ccr_promotion
    changed_error = getattr(ccr_approval_service, "PromotionInputsChanged")

    def _resolve_then_race(**kwargs):
        resolved = original_resolver(**kwargs)
        concurrent = sqlite3.connect(db_path)
        try:
            if raced_input == "review":
                concurrent.execute(
                    "INSERT INTO dre_review_edits "
                    "(dre_extraction_run_id, field_path, old_value, new_value) "
                    "VALUES (?, 'assessment_setup.summary', '', 'raced')",
                    (run_id,),
                )
            else:
                concurrent.execute(
                    "UPDATE dre_extraction_runs "
                    "SET operator_unit_factors_json = ? WHERE id = ?",
                    (json.dumps({"101": {"square_feet": "999"}}), run_id),
                )
            concurrent.commit()
        finally:
            concurrent.close()
        return resolved

    monkeypatch.setattr(
        ccr_approval_service,
        "resolve_ccr_promotion",
        _resolve_then_race,
    )

    with pytest.raises(changed_error):
        ccr_approval_service.approve_ccr_extraction_run(
            property_id=property_id,
            extraction_run_id=run_id,
            setup_type="fixed",
            reviewed_by="bob@example.com",
            connection=audit_db,
        )

    assert audit_db.execute(
        "SELECT COUNT(*) FROM assessment_setups WHERE property_id = ?",
        (property_id,),
    ).fetchone()[0] == 0


def test_resolver_merges_exact_captured_factor_snapshot(
    audit_db: sqlite3.Connection,
    monkeypatch,
) -> None:
    property_id = audit_db.execute("SELECT id FROM properties").fetchone()[0]
    run_id = audit_db.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]
    captured_raw = json.dumps({"101": {"square_feet": "1111"}})
    payload = _payload(_pool("operating"))
    payload["unit_structure"]["unit_count"] = 1
    audit_db.execute(
        "UPDATE dre_extraction_runs "
        "SET operator_unit_factors_json = ?, parsed_json = ? WHERE id = ?",
        (captured_raw, json.dumps(payload), run_id),
    )
    audit_db.commit()

    monkeypatch.setattr(
        ccr_approval_service,
        "get_operator_unit_factors",
        lambda **_: {"101": {"square_feet": "9999"}},
    )

    resolved = ccr_approval_service.resolve_ccr_promotion(
        property_id=property_id,
        extraction_run_id=run_id,
        setup_type="per_unit",
        connection=audit_db,
    )

    assert resolved.operator_factors_raw == captured_raw
    assert resolved.preview.resolved_extraction is not None
    assert resolved.preview.resolved_extraction.unit_structure.units[
        0
    ].square_feet == 1111


def test_ccr_resolver_rejects_dre_run(audit_db: sqlite3.Connection) -> None:
    property_id = audit_db.execute("SELECT id FROM properties").fetchone()[0]
    run_id = audit_db.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]
    audit_db.execute(
        "UPDATE dre_extraction_runs SET document_type = 'dre' WHERE id = ?",
        (run_id,),
    )
    audit_db.commit()

    with pytest.raises(ccr_approval_service.ExtractionRunNotFound):
        ccr_approval_service.resolve_ccr_promotion(
            property_id=property_id,
            extraction_run_id=run_id,
            setup_type="fixed",
            connection=audit_db,
        )


def test_preview_returns_structured_bob_friendly_blocker(client, db_session) -> None:
    pool = _pool("operating")
    pool["source_pages"] = []
    property_id, run_id = _seed_api_run(db_session, _payload(pool))

    response = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": "fixed"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approval_blocked"] is True
    issue = body["issues"][0]
    assert set(issue) == {
        "code",
        "severity",
        "category_key",
        "source_pages",
        "explanation",
        "recommended_operation",
        "approval_blocked",
    }
    assert issue["code"] == "CCR_POOL_SOURCE_MISSING"
    assert issue["severity"] == "error"
    assert issue["category_key"] == "operating"
    assert issue["approval_blocked"] is True
    serialized = json.dumps(issue).lower()
    assert "field_path" not in serialized
    assert "exception" not in serialized


def test_preview_structurally_blocks_partial_participant_factors(
    client,
    db_session,
) -> None:
    pool = _pool("subset")
    pool.update(
        {
            "allocation_method": "square_footage",
            "recipient_scope": "custom_unit_list",
            "selected_unit_numbers": ["A", "B"],
        }
    )
    payload = _payload(pool)
    payload["unit_structure"].update(
        {
            "unit_count": 2,
            "units": [
                {"unit_number": "A", "square_feet": "1000"},
                {"unit_number": "B", "square_feet": None},
            ],
        }
    )
    property_id, run_id = _seed_api_run(db_session, payload)

    response = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": "per_unit"},
    )

    assert response.status_code == 200, response.text
    issues = response.json()["issues"]
    factor_issue = next(
        issue for issue in issues if issue["code"] == "CCR_UNIT_FACTORS_MISSING"
    )
    assert factor_issue["category_key"] == "subset"
    assert factor_issue["approval_blocked"] is True
    assert "participating home" in factor_issue["explanation"].lower()


@pytest.mark.parametrize(
    ("allocation_context", "billing_cadence"),
    [
        ("regular_operating", "one_time"),
        ("cost_center", "one_time"),
        ("reserve_contribution", "one_time"),
        ("special_assessment", "recurring"),
    ],
)
def test_preview_blocks_unsupported_billing_combinations(
    client,
    db_session,
    allocation_context: str,
    billing_cadence: str,
) -> None:
    pool = _pool("contradictory")
    pool.update(
        {
            "allocation_context": allocation_context,
            "billing_cadence": billing_cadence,
            "pool_kind": (
                "separately_billed_special_assessment"
                if allocation_context == "special_assessment"
                else ""
            ),
        }
    )
    property_id, run_id = _seed_api_run(db_session, _payload(pool))

    response = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": "fixed"},
    )

    assert response.status_code == 200, response.text
    issue = next(
        issue
        for issue in response.json()["issues"]
        if issue["code"] == "CCR_BILLING_COMBINATION_UNSUPPORTED"
    )
    assert issue["category_key"] == "contradictory"
    assert issue["approval_blocked"] is True
    assert billing_cadence in response.json()["resolved_extraction"][
        "allocation_pools"
    ][0]["billing_cadence"]


@pytest.mark.parametrize("setup_type", ["fixed", "grouped"])
def test_preview_requires_per_unit_for_selected_home_categories(
    client,
    db_session,
    setup_type: str,
) -> None:
    pool = _pool("selected")
    pool.update(
        {
            "recipient_scope": "custom_unit_list",
            "selected_unit_numbers": ["A"],
            "participant_unit_numbers": ["A"],
        }
    )
    payload = _payload(pool)
    payload["unit_structure"].update(
        {"unit_count": 2, "units": [{"unit_number": "A"}, {"unit_number": "B"}]}
    )
    property_id, run_id = _seed_api_run(db_session, payload)

    response = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": setup_type},
    )

    assert response.status_code == 200, response.text
    issue = next(
        issue
        for issue in response.json()["issues"]
        if issue["code"] == "CCR_SETUP_TYPE_INCOMPATIBLE"
    )
    assert issue["category_key"] == "selected"
    assert issue["approval_blocked"] is True
    assert "each home" in issue["explanation"].lower()


@pytest.mark.parametrize(
    "allocation_method",
    [
        "square_footage",
        "ownership_percentage",
        "custom_factor",
        "specified_value",
    ],
)
@pytest.mark.parametrize("setup_type", ["fixed", "grouped"])
def test_preview_requires_per_unit_for_all_factor_based_categories(
    client,
    db_session,
    allocation_method: str,
    setup_type: str,
) -> None:
    pool = _pool("factor_based")
    pool["allocation_method"] = allocation_method
    payload = _payload(pool)
    payload["unit_structure"].update(
        {
            "unit_count": 2,
            "units": [
                {
                    "unit_number": "A",
                    "square_feet": "1000",
                    "ownership_percent": "40",
                    "pool_factors": [
                        {
                            "pool_key": "factor_based",
                            "factor_type": (
                                "dollar_amount"
                                if allocation_method == "specified_value"
                                else "percent"
                            ),
                            "factor_value": "4000",
                        }
                    ],
                },
                {
                    "unit_number": "B",
                    "square_feet": "1500",
                    "ownership_percent": "60",
                    "pool_factors": [
                        {
                            "pool_key": "factor_based",
                            "factor_type": (
                                "dollar_amount"
                                if allocation_method == "specified_value"
                                else "percent"
                            ),
                            "factor_value": "8000",
                        }
                    ],
                },
            ],
        }
    )
    property_id, run_id = _seed_api_run(db_session, payload)

    response = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": setup_type},
    )

    assert response.status_code == 200, response.text
    issue = next(
        issue
        for issue in response.json()["issues"]
        if issue["code"] == "CCR_SETUP_TYPE_INCOMPATIBLE"
    )
    assert issue["category_key"] == "factor_based"
    assert issue["approval_blocked"] is True


def test_specified_value_requires_complete_positive_participant_amounts(
    client,
    db_session,
) -> None:
    pool = _pool("fixed")
    pool["allocation_method"] = "specified_value"
    payload = _payload(pool)
    payload["unit_structure"].update(
        {"unit_count": 2, "units": [{"unit_number": "A"}, {"unit_number": "B"}]}
    )
    property_id, run_id = _seed_api_run(db_session, payload)
    endpoint = f"/hoa/{property_id}/ccr/extraction-runs/{run_id}"

    blocked = client.get(
        f"{endpoint}/promotion-preview",
        params={"setup_type": "per_unit"},
    )

    assert blocked.status_code == 200, blocked.text
    issue = next(
        issue
        for issue in blocked.json()["issues"]
        if issue["code"] == "CCR_SPECIFIED_VALUES_MISSING"
    )
    assert issue["category_key"] == "fixed"
    assert issue["approval_blocked"] is True

    saved = client.post(
        f"{endpoint}/factors",
        json={
            "factors": [
                {"unit_number": "A", "fixed_amounts": {"fixed": 1200}},
                {"unit_number": "B", "fixed_amounts": {"fixed": 2400}},
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    clean = client.get(
        f"{endpoint}/promotion-preview",
        params={"setup_type": "per_unit"},
    )
    assert clean.status_code == 200, clean.text
    assert "CCR_SPECIFIED_VALUES_MISSING" not in {
        item["code"] for item in clean.json()["issues"]
    }


def test_specified_value_allows_documented_zero_annual_amounts(
    client,
    db_session,
) -> None:
    pool = _pool("parking")
    pool["allocation_method"] = "specified_value"
    pool["amount_availability"] = "external_schedule"
    pool["annual_amount"] = None
    pool["monthly_amount"] = None
    payload = _payload(pool)
    payload["unit_structure"].update(
        {
            "unit_count": 2,
            "units": [{"unit_number": "201"}, {"unit_number": "202"}],
        }
    )
    property_id, run_id = _seed_api_run(db_session, payload)
    endpoint = f"/hoa/{property_id}/ccr/extraction-runs/{run_id}"

    saved = client.post(
        f"{endpoint}/factors",
        json={
            "factors": [
                {"unit_number": "201", "fixed_amounts": {"parking": 0}},
                {"unit_number": "202", "fixed_amounts": {"parking": 0}},
            ]
        },
    )
    assert saved.status_code == 200, saved.text

    preview = client.get(
        f"{endpoint}/promotion-preview",
        params={"setup_type": "per_unit"},
    )
    assert preview.status_code == 200, preview.text
    codes = {item["code"] for item in preview.json()["issues"]}
    assert "CCR_SPECIFIED_VALUES_MISSING" not in codes
    assert "CCR_SPECIFIED_VALUES_INVALID" not in codes


def test_specified_value_external_schedule_without_named_homes_promotes(
    client,
    db_session,
) -> None:
    pool = _pool("parking_space_cost_center")
    pool["allocation_method"] = "specified_value"
    pool["recipient_scope"] = "Units with an appurtenant parking space"
    pool["selected_unit_numbers"] = []
    pool["participant_unit_numbers"] = []
    pool["amount_availability"] = "external_schedule"
    pool["annual_amount"] = None
    pool["monthly_amount"] = None
    payload = _payload(pool)
    payload["unit_structure"].update(
        {
            "unit_count": 2,
            "units": [{"unit_number": "201"}, {"unit_number": "202"}],
        }
    )
    property_id, run_id = _seed_api_run(db_session, payload)
    endpoint = f"/hoa/{property_id}/ccr/extraction-runs/{run_id}"

    preview = client.get(
        f"{endpoint}/promotion-preview",
        params={"setup_type": "per_unit"},
    )
    assert preview.status_code == 200, preview.text
    codes = {item["code"] for item in preview.json()["issues"]}
    assert "CCR_SPECIFIED_VALUES_MISSING" not in codes
    assert preview.json()["approval_blocked"] is False, preview.json()["issues"]

    approved = client.post(
        f"{endpoint}/approve",
        json={"setup_type": "per_unit"},
    )
    assert approved.status_code == 200, approved.text
    rows = db_session.connection().connection.execute(
        "SELECT specified_monthly_amount FROM assessment_unit_pool_allocations "
        "WHERE assessment_setup_id = ?",
        (approved.json()["promoted_setup_id"],),
    ).fetchall()
    assert rows == []


def test_specified_value_zero_amounts_promote_when_scope_has_no_named_homes(
    client,
    db_session,
) -> None:
    pool = _pool("parking_space_cost_center")
    pool["allocation_method"] = "specified_value"
    pool["recipient_scope"] = "Units with an appurtenant parking space"
    pool["selected_unit_numbers"] = []
    pool["participant_unit_numbers"] = []
    pool["amount_availability"] = "external_schedule"
    pool["annual_amount"] = None
    pool["monthly_amount"] = None
    payload = _payload(pool)
    payload["unit_structure"].update(
        {
            "unit_count": 2,
            "units": [{"unit_number": "201"}, {"unit_number": "202"}],
        }
    )
    property_id, run_id = _seed_api_run(db_session, payload)
    endpoint = f"/hoa/{property_id}/ccr/extraction-runs/{run_id}"

    saved = client.post(
        f"{endpoint}/factors",
        json={
            "factors": [
                {
                    "unit_number": "201",
                    "fixed_amounts": {"parking_space_cost_center": 0},
                },
                {
                    "unit_number": "202",
                    "fixed_amounts": {"parking_space_cost_center": 0},
                },
            ]
        },
    )
    assert saved.status_code == 200, saved.text

    preview = client.get(
        f"{endpoint}/promotion-preview",
        params={"setup_type": "per_unit"},
    )
    assert preview.status_code == 200, preview.text
    codes = {item["code"] for item in preview.json()["issues"]}
    assert "CCR_SPECIFIED_VALUES_MISSING" not in codes
    assert "CCR_SPECIFIED_VALUES_INVALID" not in codes
    assert preview.json()["approval_blocked"] is False, preview.json()["issues"]

    approved = client.post(
        f"{endpoint}/approve",
        json={"setup_type": "per_unit"},
    )
    assert approved.status_code == 200, approved.text
    rows = db_session.connection().connection.execute(
        "SELECT specified_monthly_amount FROM assessment_unit_pool_allocations "
        "WHERE assessment_setup_id = ? ORDER BY id",
        (approved.json()["promoted_setup_id"],),
    ).fetchall()
    assert [Decimal(str(amount)) for (amount,) in rows] == [
        Decimal("0.00"),
        Decimal("0.00"),
    ]


def test_specified_value_preview_rejects_cent_level_total_mismatch(
    client,
    db_session,
) -> None:
    pool = _pool("fixed")
    pool["allocation_method"] = "specified_value"
    payload = _payload(pool)
    payload["unit_structure"].update(
        {
            "unit_count": 2,
            "units": [
                {
                    "unit_number": "A",
                    "pool_factors": [
                        {
                            "pool_key": "fixed",
                            "factor_type": "dollar_amount",
                            "factor_value": "4000",
                        }
                    ],
                },
                {
                    "unit_number": "B",
                    "pool_factors": [
                        {
                            "pool_key": "fixed",
                            "factor_type": "dollar_amount",
                            "factor_value": "7999.99",
                        }
                    ],
                },
            ],
        }
    )
    property_id, run_id = _seed_api_run(db_session, payload)

    response = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": "per_unit"},
    )

    assert response.status_code == 200, response.text
    issue = next(
        item
        for item in response.json()["issues"]
        if item["code"] == "CCR_SPECIFIED_VALUES_INVALID"
    )
    assert issue["category_key"] == "fixed"
    assert "11,999.99" in issue["explanation"]
    assert response.json()["approval_blocked"] is True


def test_clean_specified_value_preview_promotes_without_placeholders(
    client,
    db_session,
) -> None:
    pool = _pool("fixed")
    pool.update(
        {
            "allocation_method": "specified_value",
            "annual_amount": "4260",
            "monthly_amount": "355",
        }
    )
    payload = _payload(pool)
    payload["unit_structure"].update(
        {
            "unit_count": 2,
            "units": [
                {
                    "unit_number": "A",
                    "pool_factors": [
                        {
                            "pool_key": "fixed",
                            "factor_type": "dollar_amount",
                            "factor_value": "145",
                        }
                    ],
                },
                {
                    "unit_number": "B",
                    "pool_factors": [
                        {
                            "pool_key": "fixed",
                            "factor_type": "dollar_amount",
                            "factor_value": "210",
                        }
                    ],
                },
            ],
        }
    )
    property_id, run_id = _seed_api_run(db_session, payload)
    endpoint = f"/hoa/{property_id}/ccr/extraction-runs/{run_id}"

    preview = client.get(
        f"{endpoint}/promotion-preview",
        params={"setup_type": "per_unit"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["approval_blocked"] is False, preview.json()["issues"]

    approved = client.post(
        f"{endpoint}/approve",
        json={"setup_type": "per_unit"},
    )
    assert approved.status_code == 200, approved.text
    rows = db_session.connection().connection.execute(
        "SELECT specified_monthly_amount, source "
        "FROM assessment_unit_pool_allocations "
        "WHERE assessment_setup_id = ? ORDER BY id",
        (approved.json()["promoted_setup_id"],),
    ).fetchall()
    assert [(Decimal(str(amount)), source) for amount, source in rows] == [
        (Decimal("145"), "dre"),
        (Decimal("210"), "dre"),
    ]
    assert all(source != promotion.EQUAL_SPLIT_PLACEHOLDER_SOURCE for _, source in rows)


def test_preview_structures_legacy_partial_operator_roster_without_500(
    client,
    db_session,
) -> None:
    payload = _payload(_pool("operating"))
    payload["unit_structure"].update(
        {"unit_count": 3, "units": [{"unit_number": "A"}]}
    )
    property_id, run_id = _seed_api_run(db_session, payload)
    raw = db_session.connection().connection
    legacy = {
        "A": {"square_feet": "1000"},
        "B": {"square_feet": "900"},
    }
    raw.execute(
        "UPDATE dre_extraction_runs SET operator_unit_factors_json = ? WHERE id = ?",
        (json.dumps(legacy), run_id),
    )
    raw.commit()
    endpoint = f"/hoa/{property_id}/ccr/extraction-runs/{run_id}"

    response = client.get(
        f"{endpoint}/promotion-preview",
        params={"setup_type": "per_unit"},
    )

    assert response.status_code == 200, response.text
    issue = next(
        item
        for item in response.json()["issues"]
        if item["code"] == "CCR_OPERATOR_ROSTER_INCOMPLETE"
    )
    assert issue["approval_blocked"] is True
    assert "all 3 distinct homes" in issue["explanation"].lower()
    assert client.get(f"{endpoint}/factors").json() == legacy

    rejected = client.post(
        f"{endpoint}/factors",
        json={
            "factors": [
                {"unit_number": "A", "square_feet": 1000},
                {"unit_number": "B", "square_feet": 900},
            ]
        },
    )
    assert rejected.status_code == 422
    assert client.get(f"{endpoint}/factors").json() == legacy


def test_approval_refuses_exact_preview_blockers(client, db_session) -> None:
    pool = _pool("operating")
    pool["source_pages"] = []
    property_id, run_id = _seed_api_run(db_session, _payload(pool))
    raw = db_session.connection().connection

    preview = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": "fixed"},
    ).json()
    approved = client.post(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/approve",
        json={"setup_type": "fixed"},
    )

    assert approved.status_code == 422
    assert approved.json()["detail"]["issues"] == preview["issues"]
    assert raw.execute(
        "SELECT COUNT(*) FROM assessment_setups WHERE property_id = ?",
        (property_id,),
    ).fetchone()[0] == 0


def test_preview_blocks_ambiguous_ownership_percent_before_approval(
    client,
    db_session,
) -> None:
    ownership_pool = _pool("ownership")
    ownership_pool.update(
        {
            "allocation_method": "ownership_percentage",
            "source_pages": [12],
        }
    )
    payload = _payload(ownership_pool)
    payload["unit_structure"].update(
        {
            "unit_count": 2,
            "units": [
                {"unit_number": "101", "ownership_percent": "0.60"},
                {"unit_number": "102", "ownership_percent": "0.60"},
            ],
        }
    )
    property_id, run_id = _seed_api_run(db_session, payload)
    raw = db_session.connection().connection

    preview_response = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": "per_unit"},
    )
    approval_response = client.post(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/approve",
        json={"setup_type": "per_unit"},
    )

    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    issue = next(
        issue
        for issue in preview["issues"]
        if issue["code"] == "CCR_OWNERSHIP_PERCENT_AMBIGUOUS"
    )
    assert preview["approval_blocked"] is True
    assert issue["category_key"] == "ownership"
    assert issue["source_pages"] == [12]
    assert issue["approval_blocked"] is True
    assert approval_response.status_code == 422
    assert approval_response.json()["detail"]["issues"] == preview["issues"]
    assert raw.execute(
        "SELECT COUNT(*) FROM assessment_setups WHERE property_id = ?",
        (property_id,),
    ).fetchone()[0] == 0


def test_preview_normalizes_square_footage_before_ownership_validation(
    client,
    db_session,
) -> None:
    square_footage_pool = _pool("proportional")
    square_footage_pool.update(
        {
            "allocation_method": "square_footage",
            "denominator_value": None,
            "source_pages": [16],
        }
    )
    payload = _payload(square_footage_pool)
    payload["unit_structure"].update(
        {
            "unit_count": 2,
            "units": [
                {"unit_number": "101", "ownership_percent": "0.60"},
                {"unit_number": "102", "ownership_percent": "0.60"},
            ],
        }
    )
    property_id, run_id = _seed_api_run(db_session, payload)

    preview_response = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": "per_unit"},
    )
    approval_response = client.post(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/approve",
        json={"setup_type": "per_unit"},
    )

    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["resolved_extraction"]["allocation_pools"][0][
        "allocation_method"
    ] == "ownership_percentage"
    assert preview["approval_blocked"] is True
    issue = next(
        issue
        for issue in preview["issues"]
        if issue["code"] == "CCR_OWNERSHIP_PERCENT_AMBIGUOUS"
    )
    assert issue["category_key"] == "proportional"
    assert issue["source_pages"] == [16]
    assert approval_response.status_code == 422
    assert approval_response.json()["detail"]["issues"] == preview["issues"]


def test_preview_blocks_edited_pool_that_cannot_land_in_promotion(
    client,
    db_session,
) -> None:
    property_id, run_id = _seed_api_run(
        db_session,
        _payload(_pool("operating")),
    )
    raw = db_session.connection().connection
    dre_review_service.record_review_edit(
        dre_extraction_run_id=run_id,
        field_path="allocation_pools[0].allocation_method",
        old_value="equal",
        new_value="unknown",
        edited_by="bob@example.com",
        connection=raw,
    )

    preview_response = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": "fixed"},
    )
    approval_response = client.post(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/approve",
        json={"setup_type": "fixed"},
    )

    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["approval_blocked"] is True
    issue = next(
        issue
        for issue in preview["issues"]
        if issue["code"] == "CCR_EDITED_ENTITY_UNPROMOTABLE"
    )
    assert issue["category_key"] == "operating"
    assert issue["source_pages"] == [3]
    assert issue["approval_blocked"] is True
    assert approval_response.status_code == 422
    assert approval_response.json()["detail"]["issues"] == preview["issues"]
    assert raw.execute(
        "SELECT COUNT(*) FROM assessment_setups WHERE property_id = ?",
        (property_id,),
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("reason", "category_key", "source_pages"),
    [
        (
            "special-assessment pool 'structural' must use one_time billing cadence",
            "structural",
            [9],
        ),
        (
            "cost-center pool 'parking' has an unresolved allocation basis",
            "parking",
            [11],
        ),
        (
            "allocation category 'reserve' is incomplete",
            "reserve",
            [13],
        ),
    ],
)
def test_structured_issue_extracts_pool_and_category_variants(
    reason: str,
    category_key: str,
    source_pages: list[int],
) -> None:
    structural = _pool("structural")
    structural["source_pages"] = [9]
    parking = _pool("parking")
    parking["source_pages"] = [11]
    reserve = _pool("reserve")
    reserve["source_pages"] = [13]
    extraction = promotion.parse_extraction_payload(
        json.dumps(_payload(structural, parking, reserve))
    )
    assert extraction is not None

    issue = ccr_approval_service._coherence_issue(reason, extraction)

    assert issue.category_key == category_key
    assert issue.source_pages == source_pages


def test_repromotion_uses_latest_preview_candidate(client, db_session) -> None:
    property_id, run_id = _seed_api_run(db_session, _payload(_pool("operating")))
    raw = db_session.connection().connection
    first = client.post(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/approve",
        json={"setup_type": "fixed"},
    )
    assert first.status_code == 200, first.text

    dre_review_service.record_structural_operation(
        dre_extraction_run_id=run_id,
        operation={
            "operation": "update",
            "base_version": 0,
            "category_key": "operating",
            "changes": {"pool_name": "Repromoted Correction"},
        },
        reason="Apply the reviewed category name",
        edited_by="bob@example.com",
        connection=raw,
    )
    preview = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": "fixed"},
    ).json()

    repromoted = client.post(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/repromote",
        json={"setup_type": "fixed"},
    )

    assert repromoted.status_code == 200, repromoted.text
    promoted_name = raw.execute(
        "SELECT pool_name FROM allocation_pools WHERE assessment_setup_id = ?",
        (repromoted.json()["promoted_setup_id"],),
    ).fetchone()[0]
    assert promoted_name == preview["resolved_extraction"]["allocation_pools"][0][
        "pool_name"
    ]


def test_bob_can_recover_run_18_entirely_through_public_correction_endpoints(
    client,
    db_session,
) -> None:
    payload = missouri_run_18_extraction_payload()
    pools = {
        pool["pool_key"]: pool
        for pool in payload["allocation_pools"]
    }
    operating_factors = {
        unit["unit_number"]: str(index + 1)
        for index, unit in enumerate(MISSOURI_UNITS)
    }
    reserve_factor_values = ("9", "7", "5", "3", "1", "2", "4", "6", "8")
    reserve_factors = {
        unit["unit_number"]: reserve_factor_values[index]
        for index, unit in enumerate(MISSOURI_UNITS)
    }
    ownership_factors = {
        unit["unit_number"]: unit["ownership_percent"]
        for unit in MISSOURI_UNITS
    }
    assert operating_factors != reserve_factors
    assert operating_factors != ownership_factors
    assert reserve_factors != ownership_factors
    combined = {
        **pools["variable_dre_operating"],
        "pool_key": "variable_dre_exceptions",
        "pool_name": "Combined DRE Exceptions",
        "included_budget_lines": [
            *pools["variable_dre_operating"]["included_budget_lines"],
            *pools["variable_dre_reserves"]["included_budget_lines"],
        ],
    }
    equal_base = {
        **pools["equal_base"],
        "annual_amount": "72591",
        "residual_after_pool_keys": [
            "variable_dre_exceptions",
            "structural_repair_sa",
        ],
    }
    payload["allocation_pools"] = [
        equal_base,
        combined,
        pools["structural_repair_sa"],
    ]
    property_id, run_id = _seed_api_run(db_session, payload)
    edit_endpoint = f"/hoa/{property_id}/dre/extraction-runs/{run_id}/edits"
    initial_preview_response = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": "per_unit"},
    )
    assert initial_preview_response.status_code == 200, initial_preview_response.text
    initial_preview = initial_preview_response.json()
    assert initial_preview["approval_blocked"] is True
    assert {
        issue["code"] for issue in initial_preview["issues"]
    } >= {
        "CCR_DECLARED_CATEGORY_MISSING",
        "CCR_UNIT_FACTORS_MISSING",
    }

    def save_operation(operation: dict, reason: str) -> None:
        response = client.post(
            edit_endpoint,
            json={
                "field_path": "allocation_pools.$operation",
                "new_value": operation,
                "reason": reason,
            },
        )
        assert response.status_code == 200, response.text

    save_operation(
        {
            "operation": "split",
            "base_version": 0,
            "category_key": "variable_dre_exceptions",
            "pools": [
                pools["variable_dre_operating"],
                pools["variable_dre_reserves"],
            ],
        },
        "Separate recurring operating exceptions from recurring reserve contributions.",
    )
    save_operation(
        {
            "operation": "add",
            "base_version": 1,
            "category_key": "parking_cost_center",
            "pool": pools["parking_cost_center"],
        },
        "Add the separately documented recurring parking cost center.",
    )
    corrected_without_factors = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": "per_unit"},
    )
    assert corrected_without_factors.status_code == 200
    missing_factor_categories = {
        issue["category_key"]
        for issue in corrected_without_factors.json()["issues"]
        if issue["code"] == "CCR_UNIT_FACTORS_MISSING"
    }
    assert missing_factor_categories == {
        "variable_dre_operating",
        "variable_dre_reserves",
    }

    factor_response = client.post(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/factors",
        json={
            "factors": [
                {
                    "unit_number": unit["unit_number"],
                    "square_feet": unit["square_feet"],
                    "ownership_percent": unit["ownership_percent"],
                    "custom_factors": {
                        "variable_dre_operating": operating_factors[
                            unit["unit_number"]
                        ],
                        "variable_dre_reserves": reserve_factors[
                            unit["unit_number"]
                        ],
                    },
                }
                for unit in MISSOURI_UNITS
            ]
        },
    )
    assert factor_response.status_code == 200, factor_response.text

    preview_response = client.get(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/promotion-preview",
        params={"setup_type": "per_unit"},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["approval_blocked"] is False, preview["issues"]
    resolved_pools = {
        pool["pool_key"]: pool
        for pool in preview["resolved_extraction"]["allocation_pools"]
    }
    assert set(resolved_pools) == {
        "equal_base",
        "variable_dre_operating",
        "variable_dre_reserves",
        "parking_cost_center",
        "structural_repair_sa",
    }
    assert resolved_pools["equal_base"]["residual_after_pool_keys"] == [
        "variable_dre_operating",
        "variable_dre_reserves",
        "parking_cost_center",
    ]
    assert {
        key: (
            pool["allocation_context"],
            pool["billing_cadence"],
            pool["amount_availability"],
        )
        for key, pool in resolved_pools.items()
    } == {
        "equal_base": ("regular_operating", "recurring", "known"),
        "variable_dre_operating": (
            "regular_operating",
            "recurring",
            "external_schedule",
        ),
        "variable_dre_reserves": (
            "reserve_contribution",
            "recurring",
            "external_schedule",
        ),
        "parking_cost_center": ("cost_center", "recurring", "external_schedule"),
        "structural_repair_sa": (
            "special_assessment",
            "one_time",
            "operator_pending",
        ),
    }
    assert resolved_pools["structural_repair_sa"]["billing_cadence"] == "one_time"

    approved = client.post(
        f"/hoa/{property_id}/ccr/extraction-runs/{run_id}/approve",
        json={"setup_type": "per_unit"},
    )
    assert approved.status_code == 200, approved.text
    setup_id = approved.json()["promoted_setup_id"]
    raw = db_session.connection().connection
    promoted = {
        row[0]: row[1:]
        for row in raw.execute(
        "SELECT pool_key, declared_allocation_method, allocation_method, "
        "recipient_scope, pool_kind, budget_line_derivation, "
        "residual_after_pool_keys_json "
            "FROM allocation_pools WHERE assessment_setup_id = ?",
            (setup_id,),
        ).fetchall()
    }
    assert promoted == {
        "equal_base": (
            "equal",
            "equal",
            "all_units",
            None,
            "residual_default",
            '["variable_dre_operating", "variable_dre_reserves", "parking_cost_center"]',
        ),
        "variable_dre_operating": (
            "custom_factor",
            "ownership_percentage",
            "all_units",
            None,
            "explicit_lines",
            "[]",
        ),
        "variable_dre_reserves": (
            "custom_factor",
            "ownership_percentage",
            "all_units",
            None,
            "explicit_lines",
            "[]",
        ),
        "parking_cost_center": (
            "square_footage",
            "square_footage",
            "custom_unit_list",
            None,
            "explicit_lines",
            "[]",
        ),
        "structural_repair_sa": (
            "square_footage",
            "square_footage",
            "all_units",
            "separately_billed_special_assessment",
            "explicit_lines",
            "[]",
        ),
    }
    assert raw.execute(
        "SELECT COUNT(*) FROM assessment_units WHERE assessment_setup_id = ?",
        (setup_id,),
    ).fetchone()[0] == len(MISSOURI_UNITS)
    resolutions = {
        row[0]: (row[1], row[2], json.loads(row[3]))
        for row in raw.execute(
            "SELECT pool_key, status, resolved_method, factor_snapshot_json "
            "FROM allocation_resolutions WHERE assessment_setup_id = ? "
            "AND pool_key IN ('variable_dre_operating', 'variable_dre_reserves')",
            (setup_id,),
        ).fetchall()
    }
    assert set(resolutions) == {
        "variable_dre_operating",
        "variable_dre_reserves",
    }
    expected_factors = {
        "variable_dre_operating": operating_factors,
        "variable_dre_reserves": reserve_factors,
    }
    for pool_key, (status, method, snapshot) in resolutions.items():
        assert status == "approved"
        assert method == "ownership_percentage"
        assert snapshot["recipients"] == expected_factors[pool_key]

    promoted_pool_rows = raw.execute(
        "SELECT id, pool_key, pool_name, allocation_method, recipient_scope, "
        "denominator_value, display_order, pool_kind "
        "FROM allocation_pools WHERE assessment_setup_id = ? "
        "AND pool_key IN ('variable_dre_operating', 'variable_dre_reserves') "
        "ORDER BY pool_key",
        (setup_id,),
    ).fetchall()
    engine_pools = [
        PoolDefinition(
            pool_id=row[0],
            pool_key=row[1],
            pool_name=row[2],
            allocation_method=row[3],
            recipient_scope=row[4],
            denominator_value=row[5],
            display_order=row[6],
            pool_kind=row[7],
        )
        for row in promoted_pool_rows
    ]
    recipients = [
        RecipientReference(
            ref_type="unit",
            ref_id=row[0],
            label=row[1],
            square_feet=Decimal(str(row[2])),
            ownership_percent=Decimal(str(row[3])),
        )
        for row in raw.execute(
            "SELECT id, unit_number, square_feet, ownership_percent "
            "FROM assessment_units WHERE assessment_setup_id = ? ORDER BY id",
            (setup_id,),
        ).fetchall()
    ]
    executable_pools, weights, _, _, _ = (
        assessment_schedule_matrix._apply_approved_allocation_resolutions(
            connection=raw,
            setup_id=setup_id,
            pools=engine_pools,
            recipients=recipients,
            pool_custom_recipients={},
        )
    )
    assert all(
        pool.allocation_method == "ownership_percentage"
        for pool in executable_pools
    )
    for pool in executable_pools:
        rows, warnings = assessment_engine._allocate_pool(
            pool,
            Decimal("1000"),
            recipients,
            {},
            weights,
        )
        assert warnings == []
        assert len(rows) == len(MISSOURI_UNITS)
        factor_total = sum(
            (
                Decimal(value)
                for value in expected_factors[pool.pool_key].values()
            ),
            start=Decimal("0"),
        )
        assert {
            row.recipient_ref.label: row.unrounded_component_monthly
            for row in rows
        } == {
            unit_number: Decimal("1000") * Decimal(value) / factor_total
            for unit_number, value in expected_factors[pool.pool_key].items()
        }
