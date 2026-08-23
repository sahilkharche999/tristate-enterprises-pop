"""H1 surfacing — a stale pool mapping is a named, resolvable review row.

Exercises the pure summary/blocker builders (no DB) plus the matrix helper
that turns the engine's money-routing report into named operator messages.
"""
from __future__ import annotations

from types import SimpleNamespace
from decimal import Decimal

from app.services.assessment_budget_mapping_rule_service import (
    build_assessment_mapping_review_blockers,
    build_assessment_mapping_review_summary,
)
from app.disclosure_package.assessment_schedule_matrix import (
    _money_routing_issue_messages,
)
from app.assessment_engine.schemas import (
    OrphanedPoolReport,
    ZeroRecipientPoolReport,
)


def _row(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "line_key": "k",
        "line_label": "Parking Fees",
        "included_in_regular_basis": True,
        "assessment_mapping_amount": 1200.0,
        "disposition_state": "clear",
        "current_pool_key": "parking",
        "stale_pool_mapping": False,
    }
    base.update(over)
    return base


def test_stale_row_does_not_count_as_mapped_and_blocks() -> None:
    rows = [_row(stale_pool_mapping=True)]
    summary = build_assessment_mapping_review_summary(rows)
    # The stale row must not count toward the mapped total; it is unresolved.
    assert summary["mapped_regular_total"] == 0.0
    assert "Parking Fees" in summary["unresolved_required_rows"]
    assert summary["final_render_blocked"] is True


def test_clear_mapped_row_counts_and_does_not_block() -> None:
    rows = [_row(stale_pool_mapping=False)]
    summary = build_assessment_mapping_review_summary(rows)
    assert summary["mapped_regular_total"] == 1200.0
    assert summary["unresolved_required_rows"] == []


def test_stale_blocker_names_line_and_removed_pool() -> None:
    rows = [_row(stale_pool_mapping=True)]
    blockers = build_assessment_mapping_review_blockers(
        property_id=1, assessment_setup_id=1, review_rows=rows, connection=None,
    )
    assert "stale_pool_mapping" in blockers
    message = blockers["stale_pool_mapping"][0]
    assert "Parking Fees" in message
    assert "parking" in message
    assert "remap" in message.lower() or "exclude" in message.lower()


def test_routing_issue_messages_name_lines_pools_and_actions() -> None:
    result = SimpleNamespace(
        orphaned_pool_lines=[
            OrphanedPoolReport(
                pool_key="parking",
                annual_total=Decimal("1200"),
                contributing_line_labels=["parking fees"],
            )
        ],
        zero_recipient_pools=[
            ZeroRecipientPoolReport(
                pool_key="res_only",
                recipient_scope="residential_only",
                annual_total=Decimal("6000"),
                contributing_line_labels=["residential amenity"],
            )
        ],
    )
    messages = _money_routing_issue_messages(result)
    assert len(messages) == 2
    joined = " ".join(messages)
    assert "parking fees" in joined and "parking" in joined
    assert "res_only" in joined and "residential_only" in joined
    assert "residential amenity" in joined
    # Every message offers an in-app action (no dead-end block).
    for m in messages:
        assert "exclude" in m.lower() or "remap" in m.lower()
