from __future__ import annotations

import json
import logging


def _seed_review_data(db_session) -> tuple[int, int]:
    from app.ai_implementation.db.models import BudgetDraft, Property

    hoa = Property(name="Mapping API HOA", units=12, hoa_code="MAP")
    db_session.add(hoa)
    db_session.commit()
    db_session.refresh(hoa)
    raw = db_session.connection().connection
    raw.execute(
        """
        INSERT INTO assessment_setups
            (property_id, setup_type, display_mode, status)
        VALUES
            (?, 'grouped', 'grouped', 'approved')
        """,
        (hoa.id,),
    )
    setup_id = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
    raw.execute(
        """
        INSERT INTO allocation_pools
            (assessment_setup_id, pool_key, pool_name, allocation_method,
             recipient_scope, budget_line_derivation)
        VALUES
            (?, 'pool_a', 'Pool A', 'equal', 'all_units', 'explicit_lines')
        """,
        (setup_id,),
    )
    raw.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, normalized_label,
             match_type, rule_source, approval_status, review_state)
        VALUES
            (?, ?, 'pool_a', 'insurance', 'normalized_label',
             'dre_included_budget_line', 'suggested', 'pending_review')
        """,
        (hoa.id, setup_id),
    )
    raw.execute(
        "UPDATE properties SET default_assessment_setup_id = ? WHERE id = ?",
        (setup_id, hoa.id),
    )
    draft = BudgetDraft(
        property_id=hoa.id,
        status="active",
        line_items_json=json.dumps([
            {
                "label": "Insurance",
                "category": "operating",
                "annual_budget": 1200,
                "raw": {"section": "operating"},
            },
            {
                "label": "Assessment Revenue",
                "category": "income",
                "annual_budget": 1200,
                "raw": {"section": "income"},
            },
        ]),
        actor_name="tester",
    )
    db_session.add(draft)
    db_session.commit()
    return hoa.id, int(setup_id)


def _seed_evidence_review_data(db_session) -> tuple[int, int]:
    hoa_id, setup_id = _seed_review_data(db_session)
    raw = db_session.connection().connection
    raw.execute(
        """
        UPDATE budget_drafts
           SET line_items_json = ?
         WHERE property_id = ?
           AND status = 'active'
        """,
        (
            json.dumps([
                {
                    "label": "General Insurance",
                    "category": "operating",
                    "annual_budget": 1200,
                    "raw": {"section": "operating"},
                },
                {
                    "label": "Water & Sewer",
                    "category": "operating",
                    "annual_budget": 800,
                    "raw": {"section": "operating"},
                },
            ]),
            hoa_id,
        ),
    )
    raw.execute(
        """
        UPDATE assessment_budget_mapping_rules
           SET match_label = 'Insurance',
               normalized_label = 'insurance',
               rule_source = 'dre_mapping_evidence',
               source_parent_category = 'Insurance',
               assessment_type = 'prorated_variable',
               review_required = 0,
               review_reason = '',
               source_evidence_text = 'Insurance belongs to pool_a.',
               confidence = 0.95
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND pool_key = 'pool_a'
        """,
        (hoa_id, setup_id),
    )
    raw.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, match_label,
             normalized_label, match_type, rule_source, approval_status,
             review_state, source_parent_category, assessment_type,
             review_required, review_reason, source_evidence_text, confidence)
        VALUES
            (?, ?, 'pool_a', 'Water, Domestic', 'water domestic',
             'normalized_label', 'dre_mapping_evidence', 'suggested',
             'pending_review', 'Utility', 'prorated_variable', 1,
             'Current-year lines may combine sewer or pass-through billing.',
             'Water, Domestic belongs to pool_a.', 0.85)
        """,
        (hoa_id, setup_id),
    )
    db_session.commit()
    return hoa_id, setup_id


def _seed_table_first_review_data(db_session) -> tuple[int, int]:
    hoa_id, setup_id = _seed_review_data(db_session)
    raw = db_session.connection().connection
    raw.execute(
        """
        UPDATE budget_drafts
           SET line_items_json = ?
         WHERE property_id = ?
           AND status = 'active'
        """,
        (
            json.dumps([
                {
                    "label": "Insurance",
                    "category": "operating",
                    "annual_budget": 1200,
                    "proposed_amount": 1500,
                    "raw": {"section": "operating"},
                },
                {
                    "label": "Reserve - Allocation/Transfer",
                    "category": "operating",
                    "annual_budget": 400,
                    "raw": {"section": "operating"},
                },
                {
                    "label": "Roof",
                    "category": "reserve_expense",
                    "annual_budget": 275,
                    "raw": {"section": "reserve"},
                },
                {
                    "label": "Assessment Revenue",
                    "category": "income",
                    "annual_budget": 2175,
                    "raw": {"section": "income"},
                },
                {
                    "label": "Total Operating Expenses",
                    "category": "operating",
                    "annual_budget": 2175,
                    "raw": {"section": "operating"},
                },
            ]),
            hoa_id,
        ),
    )
    db_session.commit()
    return hoa_id, setup_id


def _seed_assignment_review_data(db_session) -> tuple[int, int]:
    from app.ai_implementation.db.models import BudgetDraft, Property

    hoa = Property(name="Assignment Review HOA", units=18, hoa_code="ASSIGN")
    db_session.add(hoa)
    db_session.commit()
    db_session.refresh(hoa)
    raw = db_session.connection().connection
    raw.execute(
        """
        INSERT INTO assessment_setups
            (property_id, setup_type, display_mode, status)
        VALUES
            (?, 'grouped', 'grouped', 'approved')
        """,
        (hoa.id,),
    )
    setup_id = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
    raw.executemany(
        """
        INSERT INTO allocation_pools
            (assessment_setup_id, pool_key, pool_name, allocation_method,
             recipient_scope, budget_line_derivation)
        VALUES
            (?, ?, ?, 'equal', 'all_units', ?)
        """,
        [
            (setup_id, "pool_a", "Pool A", "explicit_lines"),
            (setup_id, "pool_b", "Pool B", "residual_default"),
        ],
    )
    raw.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, match_type,
             rule_source, approval_status, review_state, budget_line_derivation)
        VALUES
            (?, ?, 'pool_b', 'remainder', 'system_remainder',
             'approved', 'ready', 'residual_default')
        """,
        (hoa.id, setup_id),
    )
    raw.execute(
        "UPDATE properties SET default_assessment_setup_id = ?, portfolio_year = 2026 WHERE id = ?",
        (setup_id, hoa.id),
    )
    draft = BudgetDraft(
        property_id=hoa.id,
        status="active",
        line_items_json=json.dumps([
            {
                "label": "Insurance",
                "category": "operating",
                "annual_budget": 1200,
                "proposed_amount": 1500,
                "raw": {"section": "operating"},
            },
            {
                "label": "Management",
                "category": "operating",
                "annual_budget": 600,
                "raw": {"section": "operating"},
            },
            {
                "label": "Landscape",
                "category": "operating",
                "annual_budget": 300,
                "raw": {"section": "operating"},
            },
            {
                "label": "Reserve - Allocation/Transfer",
                "category": "operating",
                "annual_budget": 400,
                "raw": {"section": "operating"},
            },
            {
                "label": "Roof",
                "category": "reserve_expense",
                "annual_budget": 275,
                "raw": {"section": "reserve"},
            },
            {
                "label": "Assessment Revenue",
                "category": "income",
                "annual_budget": 3075,
                "raw": {"section": "income"},
            },
        ]),
        actor_name="tester",
    )
    db_session.add(draft)
    db_session.commit()
    return hoa.id, int(setup_id)


def test_get_mapping_review_state_shows_rules_and_eligibility(client, db_session):
    hoa_id, setup_id = _seed_review_data(db_session)

    response = client.get(f"/hoa/{hoa_id}/assessment-mapping-review")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assessment_setup_id"] == setup_id
    assert body["progress"]["unresolved_count"] == 1
    assert body["rules"][0]["approval_status"] == "suggested"
    assert body["pools"][0]["pool_key"] == "pool_a"
    assert body["existing_mappings"] == []
    assert body["residual_preview"]["candidate_lines"] == []
    assert body["exemption_decisions"] == []
    assert body["reconciliation_status"]["passed"] is False
    assert body["eligibility_groups"]["assessable_expense"][0]["line_label"] == "Insurance"
    assert body["eligibility_groups"]["assessment_revenue_tieout"][0]["requires_mapping"] is False


def test_pending_rules_do_not_block_table_first_review(client, db_session):
    hoa_id, _setup_id = _seed_review_data(db_session)

    response = client.get(f"/hoa/{hoa_id}/assessment-mapping-review")

    assert response.status_code == 200, response.text
    blockers = response.json()["mapping_review_blockers"]
    assert "pending_rules" not in blockers


def test_pending_residual_rule_does_not_block_table_first_review(client, db_session):
    hoa_id, setup_id = _seed_assignment_review_data(db_session)
    raw = db_session.connection().connection
    raw.execute(
        """
        UPDATE assessment_budget_mapping_rules
           SET approval_status = 'suggested',
               review_state = 'pending_review'
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND match_type = 'remainder'
        """,
        (hoa_id, setup_id),
    )
    db_session.commit()

    response = client.get(f"/hoa/{hoa_id}/assessment-mapping-review")

    assert response.status_code == 200, response.text
    blockers = response.json()["mapping_review_blockers"]
    assert "residual_approval" not in blockers


def test_get_mapping_review_state_exposes_filtered_review_rows(client, db_session):
    hoa_id, _setup_id = _seed_table_first_review_data(db_session)

    response = client.get(f"/hoa/{hoa_id}/assessment-mapping-review")

    assert response.status_code == 200, response.text
    body = response.json()
    review_rows = {row["line_label"]: row for row in body["review_rows"]}

    assert set(review_rows) == {
        "Insurance",
        "Reserve - Allocation/Transfer",
        "Roof",
    }
    assert review_rows["Insurance"]["assessment_mapping_amount"] == 1500.0
    assert review_rows["Insurance"]["source_column_used"] == "proposed_amount"
    assert review_rows["Insurance"]["row_role"] == "current_year_operating_budget_line"
    assert review_rows["Insurance"]["included_in_regular_basis"] is True
    assert review_rows["Reserve - Allocation/Transfer"]["row_role"] == "current_year_reserve_contribution_line"
    assert review_rows["Reserve - Allocation/Transfer"]["included_in_regular_basis"] is False
    assert review_rows["Roof"]["row_role"] == "reserve_component_detail"
    assert review_rows["Roof"]["included_in_regular_basis"] is False
    assert body["progress"]["unresolved_count"] == 3


def test_residual_default_rule_surfaces_as_editable_row_candidate(client, db_session):
    hoa_id, _setup_id = _seed_assignment_review_data(db_session)

    response = client.get(f"/hoa/{hoa_id}/assessment-mapping-review")

    assert response.status_code == 200, response.text
    rows = {row["line_label"]: row for row in response.json()["review_rows"]}
    management = rows["Management"]
    assert management["recommended_pool_key"] == "pool_b"
    residual_candidates = [
        candidate
        for candidate in management["candidates"]
        if candidate["budget_line_derivation"] == "residual_default"
    ]
    assert residual_candidates
    assert residual_candidates[0]["decision_level"] == "review_required_suggestion"


def test_approve_rule_and_apply_materializes_current_budget(client, db_session):
    hoa_id, _setup_id = _seed_review_data(db_session)
    state = client.get(f"/hoa/{hoa_id}/assessment-mapping-review").json()
    rule_id = state["rules"][0]["id"]

    approved = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/rules/{rule_id}/approve",
        json={"note": "Exact current budget label."},
    )
    applied = client.post(f"/hoa/{hoa_id}/assessment-mapping-review/apply")

    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status"] == "approved"
    assert applied.status_code == 200, applied.text
    assert applied.json()["counts"]["auto_approved"] == 1
    assert applied.json()["counts"]["non_blocking"] == 1
    assert applied.json()["line_results"][0]["line_label"] == "Insurance"


def test_alias_endpoint_materializes_alias_match(client, db_session):
    hoa_id, _setup_id = _seed_review_data(db_session)

    alias = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/aliases",
        json={
            "pool_key": "pool_a",
            "dre_label": "Insurance",
            "budget_label": "Insurance",
            "note": "Current label accepted.",
        },
    )
    applied = client.post(f"/hoa/{hoa_id}/assessment-mapping-review/apply")

    assert alias.status_code == 200, alias.text
    assert applied.status_code == 200, applied.text
    assert applied.json()["counts"]["auto_approved"] == 1

    revoked = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/aliases/{alias.json()['id']}/revoke",
        json={"note": "Mistake."},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["approval_status"] == "revoked"


def test_rule_reject_disable_and_edit_endpoints(client, db_session):
    hoa_id, _setup_id = _seed_review_data(db_session)
    rule_id = client.get(f"/hoa/{hoa_id}/assessment-mapping-review").json()["rules"][0]["id"]

    edited = client.patch(
        f"/hoa/{hoa_id}/assessment-mapping-review/rules/{rule_id}",
        json={"pool_key": "pool_a", "match_label": "General Insurance", "match_type": "normalized_label", "note": "Edit label."},
    )
    rejected = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/rules/{rule_id}/reject",
        json={"note": "Wrong."},
    )
    disabled = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/rules/{rule_id}/disable",
        json={"note": "Retired."},
    )

    assert edited.status_code == 200, edited.text
    assert edited.json()["normalized_label"] == "general insurance"
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["approval_status"] == "rejected"
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["approval_status"] == "disabled"


def test_residual_preview_and_approval(client, db_session):
    hoa_id, setup_id = _seed_review_data(db_session)
    raw = db_session.connection().connection
    raw.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, match_type,
             rule_source, approval_status, review_state, budget_line_derivation)
        VALUES
            (?, ?, 'pool_a', 'remainder', 'system_remainder',
             'suggested', 'pending_review', 'residual_default')
        """,
        (hoa_id, setup_id),
    )
    db_session.commit()

    preview = client.get(f"/hoa/{hoa_id}/assessment-mapping-review/residual/preview")
    approved = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/residual/approve",
        json={"note": "Approve base pool remainder."},
    )

    assert preview.status_code == 200, preview.text
    assert preview.json()["candidate_lines"][0]["line_label"] == "Insurance"
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status"] == "approved"


def test_exemption_decision_endpoint_requires_note_for_final_state(client, db_session):
    hoa_id, setup_id = _seed_review_data(db_session)
    raw = db_session.connection().connection
    raw.execute(
        """
        INSERT INTO assessment_exemption_decisions
            (property_id, assessment_setup_id, budget_year, pool_key, exemption_state)
        VALUES
            (?, ?, 2026, 'pool_a', 'pending_review')
        """,
        (hoa_id, setup_id),
    )
    db_session.commit()

    missing_note = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/exemptions/pool_a",
        json={"exemption_state": "inactive", "budget_year": 2026},
    )
    decided = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/exemptions/pool_a",
        json={
            "exemption_state": "inactive",
            "budget_year": 2026,
            "note": "Not applicable this year.",
        },
    )

    assert missing_note.status_code == 422
    assert decided.status_code == 200, decided.text
    assert decided.json()["exemption_state"] == "inactive"


def test_get_mapping_review_state_includes_line_review_items(client, db_session):
    hoa_id, _setup_id = _seed_evidence_review_data(db_session)

    response = client.get(f"/hoa/{hoa_id}/assessment-mapping-review")

    assert response.status_code == 200, response.text
    body = response.json()
    insurance = next(
        item for item in body["line_review_items"] if item["line_label"] == "General Insurance"
    )
    water = next(
        item for item in body["line_review_items"] if item["line_label"] == "Water & Sewer"
    )
    assert insurance["status"] == "suggested"
    assert insurance["candidates"][0]["decision_level"] == "safe_suggestion"
    assert insurance["candidates"][0]["source_evidence_text"] == "Insurance belongs to pool_a."
    assert water["candidates"][0]["decision_level"] == "review_required_suggestion"


def test_row_assignment_and_disposition_endpoints_persist_review_state_and_summary(client, db_session):
    hoa_id, setup_id = _seed_assignment_review_data(db_session)
    state = client.get(f"/hoa/{hoa_id}/assessment-mapping-review").json()
    rows = {row["line_label"]: row for row in state["review_rows"]}

    assign_insurance = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/rows/assign",
        json={"line_key": rows["Insurance"]["line_key"], "pool_key": "pool_a", "note": "Map insurance directly."},
    )
    assign_management = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/rows/assign",
        json={"line_key": rows["Management"]["line_key"], "pool_key": "pool_b", "note": "Map management directly."},
    )
    pending_split = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/rows/disposition",
        json={"line_key": rows["Landscape"]["line_key"], "disposition_state": "pending_split", "note": "Needs split review."},
    )
    reserve_detail = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/rows/disposition",
        json={"line_key": rows["Roof"]["line_key"], "disposition_state": "reserve_detail", "note": "Reserve component detail."},
    )
    excluded = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/rows/disposition",
        json={"line_key": rows["Reserve - Allocation/Transfer"]["line_key"], "disposition_state": "excluded_non_regular", "note": "Keep out of regular basis."},
    )

    assert assign_insurance.status_code == 200, assign_insurance.text
    assert assign_management.status_code == 200, assign_management.text
    assert pending_split.status_code == 200, pending_split.text
    assert reserve_detail.status_code == 200, reserve_detail.text
    assert excluded.status_code == 200, excluded.text

    refreshed = client.get(f"/hoa/{hoa_id}/assessment-mapping-review").json()
    refreshed_rows = {row["line_label"]: row for row in refreshed["review_rows"]}
    summary = refreshed["reconciliation_summary"]

    assert refreshed_rows["Insurance"]["current_status"] == "mapped"
    assert refreshed_rows["Insurance"]["current_pool_key"] == "pool_a"
    assert refreshed_rows["Management"]["current_status"] == "mapped"
    assert refreshed_rows["Management"]["current_pool_key"] == "pool_b"
    assert refreshed_rows["Landscape"]["current_status"] == "pending_split"
    assert refreshed_rows["Landscape"]["included_in_regular_basis"] is False
    assert refreshed_rows["Roof"]["current_status"] == "reserve_detail"
    assert refreshed_rows["Reserve - Allocation/Transfer"]["current_status"] == "excluded_non_regular"

    assert summary["mapped_regular_total"] == 2100.0
    assert summary["pending_split_total"] == 300.0
    assert summary["target_regular_assessment_basis"] == 2100.0
    assert summary["difference"] == 0.0
    assert summary["final_render_blocked"] is True
    assert summary["unresolved_required_rows"] == []

    residual = client.get(f"/hoa/{hoa_id}/assessment-mapping-review/residual/preview")
    assert residual.status_code == 200, residual.text
    assert residual.json()["candidate_lines"] == []

    raw = db_session.connection().connection
    disposition_rows = raw.execute(
        """
        SELECT review_line_key, disposition_state
          FROM assessment_review_row_dispositions
         WHERE property_id = ?
           AND assessment_setup_id = ?
         ORDER BY review_line_key
        """,
        (hoa_id, setup_id),
    ).fetchall()
    audit_count = raw.execute(
        """
        SELECT COUNT(*)
          FROM assessment_review_row_audit_events
         WHERE property_id = ?
           AND assessment_setup_id = ?
        """,
        (hoa_id, setup_id),
    ).fetchone()[0]

    assert len(disposition_rows) == 3
    assert audit_count == 5


def test_row_assignment_creates_reusable_operator_rule_for_future_reviews(client, db_session):
    hoa_id, setup_id = _seed_assignment_review_data(db_session)
    state = client.get(f"/hoa/{hoa_id}/assessment-mapping-review").json()
    management = next(row for row in state["review_rows"] if row["line_label"] == "Management")

    assigned = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/rows/assign",
        json={
            "line_key": management["line_key"],
            "pool_key": "pool_b",
            "note": "Management belongs in base pool.",
        },
    )

    assert assigned.status_code == 200, assigned.text
    raw = db_session.connection().connection
    learned_rule = raw.execute(
        """
        SELECT pool_key, match_label, normalized_label, match_type,
               rule_source, approval_status, review_state, budget_line_derivation
          FROM assessment_budget_mapping_rules
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND normalized_label = 'management'
           AND active = 1
        """,
        (hoa_id, setup_id),
    ).fetchone()
    assert learned_rule == (
        "pool_b",
        "Management",
        "management",
        "normalized_label",
        "operator",
        "approved",
        "ready",
        "explicit_lines",
    )


def test_assigning_residual_pool_uses_same_row_assignment_flow(client, db_session):
    hoa_id, _setup_id = _seed_assignment_review_data(db_session)
    state = client.get(f"/hoa/{hoa_id}/assessment-mapping-review").json()
    management = next(row for row in state["review_rows"] if row["line_label"] == "Management")

    assigned = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/rows/assign",
        json={
            "line_key": management["line_key"],
            "pool_key": "pool_b",
            "note": "Accept residual/base recommendation.",
        },
    )
    refreshed = client.get(f"/hoa/{hoa_id}/assessment-mapping-review").json()
    refreshed_management = next(
        row for row in refreshed["review_rows"] if row["line_label"] == "Management"
    )

    assert assigned.status_code == 200, assigned.text
    assert refreshed_management["current_pool_key"] == "pool_b"
    assert refreshed_management["current_status"] == "mapped"


def test_clear_disposition_restores_row_to_regular_review(client, db_session):
    hoa_id, _setup_id = _seed_assignment_review_data(db_session)
    state = client.get(f"/hoa/{hoa_id}/assessment-mapping-review").json()
    landscape = next(row for row in state["review_rows"] if row["line_label"] == "Landscape")

    marked = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/rows/disposition",
        json={"line_key": landscape["line_key"], "disposition_state": "pending_split", "note": "Split first."},
    )
    cleared = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/rows/disposition",
        json={"line_key": landscape["line_key"], "disposition_state": "clear", "note": "Return to normal review."},
    )

    assert marked.status_code == 200, marked.text
    assert cleared.status_code == 200, cleared.text

    refreshed = client.get(f"/hoa/{hoa_id}/assessment-mapping-review").json()
    refreshed_landscape = next(row for row in refreshed["review_rows"] if row["line_label"] == "Landscape")

    assert refreshed_landscape["current_status"] == "suggested"
    assert refreshed_landscape["recommended_pool_key"] == "pool_b"
    assert refreshed_landscape["included_in_regular_basis"] is True


def test_approve_line_review_suggestion_creates_alias_and_current_year_mapping(client, db_session):
    hoa_id, _setup_id = _seed_evidence_review_data(db_session)
    state = client.get(f"/hoa/{hoa_id}/assessment-mapping-review").json()
    insurance = next(
        item for item in state["line_review_items"] if item["line_label"] == "General Insurance"
    )
    candidate = insurance["candidates"][0]

    approved = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/lines/approve",
        json={
            "rule_id": candidate["rule_id"],
            "line_label": insurance["line_label"],
            "normalized_label": insurance["normalized_label"],
            "section": insurance["section"],
            "category": insurance["category"],
            "fund_type": insurance["fund_type"],
            "account_code": insurance["account_code"],
        },
    )

    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["alias_created"] is True
    assert body["mapping_created"] is True
    assert body["approval_status"] == "approved"

    refreshed = client.get(f"/hoa/{hoa_id}/assessment-mapping-review").json()
    assert not any(
        item["line_label"] == "General Insurance" and item["status"] != "mapped"
        for item in refreshed["line_review_items"]
    )
    assert refreshed["existing_mappings"][0]["pool_key"] == "pool_a"


def _seed_ai_assist_review_data(db_session) -> tuple[int, int]:
    from app.ai_implementation.db.models import BudgetDraft, Property

    hoa = Property(name="AI Mapping HOA", units=20, hoa_code="AIMAP")
    db_session.add(hoa)
    db_session.commit()
    db_session.refresh(hoa)
    raw = db_session.connection().connection
    raw.execute(
        """
        INSERT INTO assessment_setups
            (property_id, setup_type, display_mode, status)
        VALUES
            (?, 'grouped', 'grouped', 'approved')
        """,
        (hoa.id,),
    )
    setup_id = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
    raw.executemany(
        """
        INSERT INTO allocation_pools
            (assessment_setup_id, pool_key, pool_name, allocation_method,
             recipient_scope, budget_line_derivation)
        VALUES
            (?, ?, ?, ?, ?, ?)
        """,
        [
            (setup_id, "variable_costs", "Variable Costs", "square_footage", "all_units", "explicit_lines"),
            (setup_id, "equal_costs", "Equal Costs", "equal", "all_units", "residual_default"),
            (setup_id, "exempted_costs", "Exempted Costs", "equal", "all_units", "explicit_lines"),
        ],
    )
    raw.executemany(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, match_label,
             normalized_label, match_type, rule_source, approval_status,
             review_state, source_parent_category, assessment_type,
             review_required, review_reason, source_evidence_text,
             confidence, budget_line_derivation)
        VALUES
            (?, ?, ?, ?, ?, 'normalized_label', 'dre_mapping_evidence',
             'suggested', 'pending_review', ?, ?, ?, ?, ?, ?, 'explicit_lines')
        """,
        [
            (
                hoa.id,
                setup_id,
                "variable_costs",
                "Insurance",
                "insurance",
                "Insurance",
                "prorated_variable",
                0,
                "",
                "Insurance belongs to variable_costs.",
                0.95,
            ),
            (
                hoa.id,
                setup_id,
                "variable_costs",
                "Water, Domestic",
                "water domestic",
                "Utility",
                "prorated_variable",
                1,
                "Current-year lines may combine sewer or pass-through billing.",
                "Water, Domestic belongs to variable_costs.",
                0.85,
            ),
            (
                hoa.id,
                setup_id,
                "exempted_costs",
                "Landscape Exemption",
                "landscape exemption",
                "Landscaping",
                "exemption_credit",
                1,
                "2792.16(c) applicability must be confirmed each year.",
                "Landscape Exemption belongs to exempted_costs.",
                0.92,
            ),
        ],
    )
    raw.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, match_type,
             rule_source, approval_status, review_state, budget_line_derivation)
        VALUES
            (?, ?, 'equal_costs', 'remainder', 'system_remainder',
             'suggested', 'pending_review', 'residual_default')
        """,
        (hoa.id, setup_id),
    )
    raw.execute(
        """
        INSERT INTO assessment_exemption_decisions
            (property_id, assessment_setup_id, budget_year, pool_key, exemption_state)
        VALUES
            (?, ?, 2026, 'exempted_costs', 'pending_review')
        """,
        (hoa.id, setup_id),
    )
    raw.execute(
        "UPDATE properties SET default_assessment_setup_id = ? WHERE id = ?",
        (setup_id, hoa.id),
    )
    draft = BudgetDraft(
        property_id=hoa.id,
        status="active",
        line_items_json=json.dumps([
            {
                "label": "55000 - General Insurance",
                "account_code": "55000",
                "category": "operating",
                "annual_budget": 1200,
                "raw": {"section": "operating"},
            },
            {
                "label": "General Insurance",
                "category": "operating",
                "annual_budget": 1200,
                "raw": {"section": "operating"},
            },
            {
                "label": "Water & Sewer",
                "category": "operating",
                "annual_budget": 800,
                "raw": {"section": "operating"},
            },
            {
                "label": "Landscape Exemption",
                "category": "operating",
                "annual_budget": 300,
                "raw": {"section": "operating"},
            },
            {
                "label": "Clubhouse Cleaning",
                "category": "operating",
                "annual_budget": 500,
                "raw": {"section": "operating"},
            },
            {
                "label": "Assessment Revenue",
                "category": "income",
                "annual_budget": 2800,
                "raw": {"section": "income"},
            },
        ]),
        actor_name="tester",
    )
    db_session.add(draft)
    db_session.commit()
    return hoa.id, int(setup_id)


def test_ai_analyze_groups_safe_review_exclude_and_residual(client, db_session, monkeypatch):
    hoa_id, _setup_id = _seed_ai_assist_review_data(db_session)

    async def _fake_call_llm(messages, response_schema, temperature=0.3, timeout=10.0):
        payload = json.loads(str(messages[1]["content"]))
        context = payload["context"]
        joined = "\n".join(str(message["content"]) for message in messages)
        assert "variable_costs" in joined
        assert "equal_costs" in joined
        assert "exempted_costs" in joined
        assert "General Insurance" in joined
        assert "Water & Sewer" in joined
        assert "Landscape Exemption" in joined
        assert "Clubhouse Cleaning" in joined
        assert "Do not invent pools" in joined
        assert "residual_default" in joined
        assert "approved_dre_setup_json" not in context
        assert "existing_mappings" not in context
        assert all(
            row["included_in_regular_basis"] and row["current_status"] != "mapped"
            for row in context["review_rows"]
        )
        return response_schema.model_validate(
            {
                "available": True,
                "reasons": [],
                "safe_to_stage": [
                    {
                        "line_label": "General Insurance",
                        "normalized_label": "general insurance",
                        "section": "operating",
                        "category": "operating",
                        "fund_type": "operating",
                        "account_code": None,
                        "suggested_pool_key": "variable_costs",
                        "action_kind": "approve_line_suggestion",
                        "confidence": 0.96,
                        "explanation": "Insurance evidence matches the variable pool.",
                        "evidence_refs": [{"source_type": "rule", "rule_id": 1, "page_numbers": [6]}],
                    }
                ],
                "needs_decision": [
                    {
                        "subject_type": "budget_line",
                        "line_label": "Water & Sewer",
                        "normalized_label": "water sewer",
                        "section": "operating",
                        "category": "operating",
                        "fund_type": "operating",
                        "account_code": None,
                        "pool_key": None,
                        "options": [
                            {"pool_key": "variable_costs", "label": "Variable Costs"},
                            {"pool_key": "equal_costs", "label": "Equal Costs"},
                        ],
                        "recommended_pool_key": "variable_costs",
                        "explanation": "Water may include sewer or pass-through billing.",
                        "evidence_refs": [{"source_type": "rule", "rule_id": 2, "page_numbers": [6]}],
                        "blocker_kind": "review_required_mapping",
                    }
                ],
                "exclude_from_mapping": [
                    {
                        "line_label": "Landscape Exemption",
                        "normalized_label": "landscape exemption",
                        "section": "operating",
                        "category": "operating",
                        "fund_type": "operating",
                        "account_code": None,
                        "exclusion_kind": "exemption_or_credit",
                        "explanation": "Exemption applicability must be confirmed yearly.",
                        "evidence_refs": [{"source_type": "rule", "rule_id": 3, "page_numbers": [6]}],
                    }
                ],
                "residual_equal_preview": {
                    "residual_pool_key": "equal_costs",
                    "candidate_lines": [
                        {
                            "line_label": "Clubhouse Cleaning",
                            "normalized_label": "clubhouse cleaning",
                            "section": "operating",
                            "category": "operating",
                            "fund_type": "operating",
                            "account_code": None,
                            "amount": 500,
                            "reason": "Remaining assessable expense after explicit pools.",
                        }
                    ],
                    "blocked_lines": [
                        {
                            "line_label": "Landscape Exemption",
                            "normalized_label": "landscape exemption",
                            "section": "operating",
                            "category": "operating",
                            "fund_type": "operating",
                            "account_code": None,
                            "reason": "Exemption-related line requires operator review.",
                        }
                    ],
                    "explanation": "Equal pool gets remaining eligible lines after explicit variable/exemption handling.",
                },
                "audit": {
                    "model_name": "gemini-test-model",
                    "prompt_version": "1.0.0",
                    "prompt_sha256": "abc123",
                },
            }
        )

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.call_llm", _fake_call_llm)

    response = client.post(f"/hoa/{hoa_id}/assessment-mapping-review/analyze", json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["reasons"] == []
    assert [item["line_label"] for item in body["safe_to_stage"]] == ["General Insurance"]
    assert body["safe_to_stage"][0]["suggested_pool_key"] == "variable_costs"
    assert body["safe_to_stage"][0]["action_kind"] == "approve_line_suggestion"
    assert [item["line_label"] for item in body["needs_decision"]] == ["Water & Sewer"]
    assert body["needs_decision"][0]["options"][0]["pool_key"] == "variable_costs"
    assert [item["line_label"] for item in body["exclude_from_mapping"]] == ["Landscape Exemption"]
    assert body["residual_equal_preview"]["residual_pool_key"] == "equal_costs"
    assert [item["line_label"] for item in body["residual_equal_preview"]["candidate_lines"]] == ["Clubhouse Cleaning"]
    assert body["audit"]["prompt_version"] == "1.0.0"


def test_ai_analyze_timeout_reports_visible_unavailable_reason(client, db_session, monkeypatch):
    hoa_id, _setup_id = _seed_ai_assist_review_data(db_session)

    async def _timeout_call_llm(messages, response_schema, temperature=0.3, timeout=10.0):
        raise TimeoutError("request timed out")

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.call_llm", _timeout_call_llm)

    response = client.post(f"/hoa/{hoa_id}/assessment-mapping-review/analyze", json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is False
    assert body["reasons"] == ["analysis_unavailable: timeout"]


def test_ai_analyze_logs_context_metrics_for_debugging(
    client,
    db_session,
    monkeypatch,
    caplog,
):
    hoa_id, _setup_id = _seed_ai_assist_review_data(db_session)

    async def _fake_call_llm(messages, response_schema, temperature=0.3, timeout=10.0):
        return response_schema.model_validate(
            {
                "available": True,
                "reasons": [],
                "safe_to_stage": [],
                "needs_decision": [],
                "exclude_from_mapping": [],
                "residual_equal_preview": {
                    "residual_pool_key": "equal_costs",
                    "candidate_lines": [],
                    "blocked_lines": [],
                    "explanation": "",
                },
                "audit": {
                    "model_name": "gemini-test-model",
                    "prompt_version": "1.0.0",
                    "prompt_sha256": "abc123",
                },
            }
        )

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.call_llm", _fake_call_llm)
    monkeypatch.setattr("app.services.assessment_mapping_ai_review_service.settings.GEMINI_MODEL", "gemini-test-model")

    with caplog.at_level(logging.INFO, logger="app.services.assessment_mapping_ai_review_service"):
        response = client.post(f"/hoa/{hoa_id}/assessment-mapping-review/analyze", json={})

    assert response.status_code == 200, response.text
    messages = [record.getMessage() for record in caplog.records]
    start_log = next(
        message
        for message in messages
        if "assessment mapping ai analyze start" in message
    )
    assert "model=gemini-test-model" in start_log
    assert "payload_bytes=" in start_log
    assert "budget_lines=" in start_log
    assert "review_rows=" in start_log
    assert "line_review_items=" in start_log
    assert "rules=" in start_log
    assert "aliases=" in start_log
    assert "blocked_matches=" in start_log
    assert "reasons=" in start_log


def test_ai_apply_safe_creates_alias_and_mapping_without_touching_exemption_decisions(
    client,
    db_session,
    monkeypatch,
):
    hoa_id, setup_id = _seed_ai_assist_review_data(db_session)

    async def _fake_call_llm(messages, response_schema, temperature=0.3, timeout=10.0):
        return response_schema.model_validate(
            {
                "available": True,
                "reasons": [],
                "safe_to_stage": [
                    {
                        "line_label": "General Insurance",
                        "normalized_label": "general insurance",
                        "section": "operating",
                        "category": "operating",
                        "fund_type": "operating",
                        "account_code": None,
                        "suggested_pool_key": "variable_costs",
                        "action_kind": "approve_line_suggestion",
                        "confidence": 0.96,
                        "explanation": "Insurance evidence matches the variable pool.",
                        "evidence_refs": [{"source_type": "rule", "rule_id": 1, "page_numbers": [6]}],
                    }
                ],
                "needs_decision": [],
                "exclude_from_mapping": [],
                "residual_equal_preview": {
                    "residual_pool_key": "equal_costs",
                    "candidate_lines": [],
                    "blocked_lines": [],
                    "explanation": "",
                },
                "audit": {
                    "model_name": "gemini-test-model",
                    "prompt_version": "1.0.0",
                    "prompt_sha256": "abc123",
                },
            }
        )

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.call_llm", _fake_call_llm)

    analyzed = client.post(f"/hoa/{hoa_id}/assessment-mapping-review/analyze", json={})
    assert analyzed.status_code == 200, analyzed.text

    applied = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/analyze/apply-safe",
        json={"safe_to_stage": analyzed.json()["safe_to_stage"]},
    )
    repeated = client.post(
        f"/hoa/{hoa_id}/assessment-mapping-review/analyze/apply-safe",
        json={"safe_to_stage": analyzed.json()["safe_to_stage"]},
    )

    assert applied.status_code == 200, applied.text
    assert repeated.status_code == 200, repeated.text

    refreshed = client.get(f"/hoa/{hoa_id}/assessment-mapping-review").json()
    assert any(
        row["budget_line_normalized_label"] == "general insurance"
        and row["pool_key"] == "variable_costs"
        for row in refreshed["existing_mappings"]
    )
    assert any(
        alias["budget_label"] == "General Insurance"
        and alias["pool_key"] == "variable_costs"
        for alias in refreshed["aliases"]
    )
    assert refreshed["exemption_decisions"][0]["exemption_state"] == "pending_review"

    raw = db_session.connection().connection
    alias_count = raw.execute(
        """
        SELECT COUNT(*)
          FROM assessment_mapping_aliases
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND pool_key = 'variable_costs'
           AND normalized_budget_label = 'general insurance'
           AND active = 1
        """,
        (hoa_id, setup_id),
    ).fetchone()[0]
    mapping_count = raw.execute(
        """
        SELECT COUNT(*)
          FROM budget_line_pool_mappings
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND budget_line_normalized_label = 'general insurance'
           AND active = 1
        """,
        (hoa_id, setup_id),
    ).fetchone()[0]
    assert alias_count == 1
    assert mapping_count == 1


def test_mapping_analysis_schema_accepts_gemini_shorthand_lists() -> None:
    from app.services.assessment_mapping_ai_review_service import MappingAnalysisResponse

    parsed = MappingAnalysisResponse.model_validate(
        {
            "available": True,
            "reasons": [],
            "safe_to_stage": [],
            "needs_decision": [
                {
                    "subject_type": "budget_line",
                    "line_label": "Water & Sewer",
                    "options": ["variable_costs", "equal_costs"],
                    "recommended_pool_key": "variable_costs",
                    "explanation": "Needs review.",
                    "blocker_kind": "review_required_mapping",
                }
            ],
            "exclude_from_mapping": [],
            "residual_equal_preview": {
                "residual_pool_key": "equal_costs",
                "candidate_lines": ["Management Service", "Printing & Mailing"],
                "blocked_lines": ["Reserve - Allocation/Transfer"],
                "explanation": "Remaining lines for equal pool.",
            },
            "audit": {
                "model_name": "gemini-flash-latest",
                "prompt_version": "1.0.0",
                "prompt_sha256": "abc123",
            },
        }
    )

    assert parsed.needs_decision[0].options[0].pool_key == "variable_costs"
    assert parsed.needs_decision[0].options[0].label == "variable_costs"
    assert parsed.residual_equal_preview.candidate_lines[0].line_label == "Management Service"
    assert parsed.residual_equal_preview.blocked_lines[0].line_label == "Reserve - Allocation/Transfer"
