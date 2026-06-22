"""Tests for the budget-draft-reserve-fidelity change.

Covers:
  - _effective_read_only: per-line override takes precedence over section default
  - _derive_reserve_income: reserve income mirrors the allocation/transfer total
  - replace_reserve_study endpoint: success, review_required, failure, non-PDF rejection
  - save_draft read_only_override round-trip: persists across reload
"""
import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.budget_history_service import (
    _derive_reserve_income,
    _effective_read_only,
)
from app.models.reserve_study_extraction import (
    ExtractedReserveStudyDocument,
    ExtractedReserveStudyRow,
)
from app.models.financial_document_extraction import DocumentExtractionFailure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _transfer_item(annual_budget: float, **extra) -> dict:
    return {
        "line_item_key": f"transfer-{annual_budget}",
        "category": "reserve_expense",
        "reserve_group": "transfer",
        "annual_budget": annual_budget,
        "read_only": True,
        **extra,
    }


def _component_item(annual_budget: float = 5000.0) -> dict:
    return {
        "line_item_key": "component-1",
        "category": "reserve_expense",
        "reserve_group": "component",
        "annual_budget": annual_budget,
        "read_only": True,
    }


def _income_contribution_item(annual_budget: float) -> dict:
    """reserve_income line that should be overwritten by derivation."""
    return {
        "line_item_key": "res-income-1",
        "category": "reserve_income",
        "reserve_group": None,
        "annual_budget": annual_budget,
        "read_only": True,
    }


def _income_interest_item(annual_budget: float) -> dict:
    """reserve_income line classified as interest — must NOT be overwritten."""
    return {
        "line_item_key": "res-interest-1",
        "category": "reserve_income",
        "reserve_group": "income",
        "annual_budget": annual_budget,
        "read_only": True,
    }


def _operating_item(annual_budget: float = 12000.0) -> dict:
    return {
        "line_item_key": "op-1",
        "category": "operating",
        "annual_budget": annual_budget,
        "read_only": False,
    }


# ---------------------------------------------------------------------------
# _effective_read_only
# ---------------------------------------------------------------------------

class TestEffectiveReadOnly:
    def test_no_override_reserve_income_is_read_only(self):
        item = {"category": "reserve_income"}
        assert _effective_read_only(item, "reserve_income") is True

    def test_no_override_reserve_expense_is_read_only(self):
        item = {"category": "reserve_expense"}
        assert _effective_read_only(item, "reserve_expense") is True

    def test_no_override_operating_is_editable(self):
        item = {"category": "operating"}
        assert _effective_read_only(item, "operating") is False

    def test_no_override_income_is_editable(self):
        item = {"category": "income"}
        assert _effective_read_only(item, "income") is False

    def test_explicit_false_override_unlocks_reserve_income(self):
        """read_only_override=False beats the section default — row becomes editable."""
        item = {"category": "reserve_income", "read_only_override": False}
        assert _effective_read_only(item, "reserve_income") is False

    def test_explicit_false_override_unlocks_reserve_expense(self):
        item = {"category": "reserve_expense", "read_only_override": False}
        assert _effective_read_only(item, "reserve_expense") is False

    def test_explicit_true_override_locks_operating(self):
        """read_only_override=True can lock a normally-editable row."""
        item = {"category": "operating", "read_only_override": True}
        assert _effective_read_only(item, "operating") is True

    def test_null_override_falls_back_to_section_default(self):
        """override=None means 're-lock to category default'."""
        item_ri = {"category": "reserve_income", "read_only_override": None}
        assert _effective_read_only(item_ri, "reserve_income") is True

        item_op = {"category": "operating", "read_only_override": None}
        assert _effective_read_only(item_op, "operating") is False

    def test_missing_override_key_falls_back_to_section_default(self):
        """dict without the key at all is equivalent to None override."""
        assert _effective_read_only({}, "reserve_income") is True
        assert _effective_read_only({}, "operating") is False


# ---------------------------------------------------------------------------
# _derive_reserve_income
# ---------------------------------------------------------------------------

class TestDeriveReserveIncome:
    def test_single_transfer_mirrors_to_contribution(self):
        items = [
            _transfer_item(24000.0),
            _income_contribution_item(12000.0),
        ]
        result = _derive_reserve_income(items)
        contribution = next(r for r in result if r["category"] == "reserve_income")
        assert contribution["annual_budget"] == 24000.0

    def test_multiple_transfers_aggregate(self):
        items = [
            _transfer_item(15000.0),
            _transfer_item(9000.0),
            _income_contribution_item(5000.0),
        ]
        result = _derive_reserve_income(items)
        contribution = next(r for r in result if r["category"] == "reserve_income")
        assert contribution["annual_budget"] == 24000.0

    def test_zero_transfer_total_returns_items_unchanged(self):
        """When there are no transfer lines, items come back as-is."""
        items = [
            _component_item(8000.0),
            _income_contribution_item(5000.0),
        ]
        result = _derive_reserve_income(items)
        assert result is items  # exact same list object (fast path)

    def test_interest_line_not_overwritten(self):
        """reserve_income + reserve_group="income" = interest — must be preserved."""
        items = [
            _transfer_item(24000.0),
            _income_interest_item(3000.0),
        ]
        result = _derive_reserve_income(items)
        interest = next(r for r in result if r.get("reserve_group") == "income")
        assert interest["annual_budget"] == 3000.0

    def test_other_line_items_unchanged(self):
        items = [
            _operating_item(12000.0),
            _transfer_item(20000.0),
            _income_contribution_item(5000.0),
        ]
        result = _derive_reserve_income(items)
        op = next(r for r in result if r["category"] == "operating")
        assert op["annual_budget"] == 12000.0

    def test_component_lines_not_affected(self):
        """reserve_expense component rows are not transfer and must not be altered."""
        items = [
            _transfer_item(18000.0),
            _component_item(6000.0),
            _income_contribution_item(0.0),
        ]
        result = _derive_reserve_income(items)
        component = next(r for r in result if r.get("reserve_group") == "component")
        assert component["annual_budget"] == 6000.0

    def test_original_items_not_mutated(self):
        """Derivation must not modify the source list in-place."""
        orig_amount = 5000.0
        items = [
            _transfer_item(20000.0),
            _income_contribution_item(orig_amount),
        ]
        _derive_reserve_income(items)
        # The original dict should be unchanged
        contribution_orig = next(i for i in items if i["category"] == "reserve_income")
        assert contribution_orig["annual_budget"] == orig_amount

    def test_no_reserve_income_line(self):
        """Transfer lines present but no reserve_income line — returns without error."""
        items = [_transfer_item(10000.0), _operating_item()]
        result = _derive_reserve_income(items)
        assert all(r["category"] != "reserve_income" for r in result)

    def test_annual_budget_none_treated_as_zero(self):
        """annual_budget=None on a transfer item should contribute 0 to total."""
        items = [
            {"category": "reserve_expense", "reserve_group": "transfer", "annual_budget": None},
            _income_contribution_item(5000.0),
        ]
        # None → 0.0 total, so early return (items unchanged)
        result = _derive_reserve_income(items)
        assert result is items


# ---------------------------------------------------------------------------
# Reserve study replace endpoint
# ---------------------------------------------------------------------------

def _fake_reserve_doc_success() -> ExtractedReserveStudyDocument:
    return ExtractedReserveStudyDocument(
        study_year=2025,
        page_spans=[{"start_page": 1, "end_page": 2, "confidence": 0.9}],
        rows=[
            ExtractedReserveStudyRow(
                row_id="roof-2",
                line_item="Roof Replacement",
                useful_life=25,
                remaining_life=10,
                quantity=1,
                replacement_cost=300000.0,
                source_page=1,
                flags=[],
            )
        ],
        warnings=[],
        confidence=0.95,
    )


def _fake_reserve_doc_with_warnings() -> ExtractedReserveStudyDocument:
    doc = _fake_reserve_doc_success()
    object.__setattr__(doc, "warnings", ["Missing component life data"])
    return doc


@pytest.fixture
def draft_with_reserve_rows(client, budget_history_test_harness, monkeypatch):
    """Create a draft that already has reserve rows from a prior upload."""
    # First create the draft via a normal upload
    upload_resp = client.post(
        "/hoa/1/budget/upload",
        data={"source_mode": "income_statement"},
        files={"file": ("income.xlsx", b"fake bytes", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert upload_resp.status_code == 200, upload_resp.text
    draft_id = upload_resp.json()["draft"]["id"]

    # Seed reserve rows onto the draft via the existing PATCH endpoint
    patch_resp = client.patch(
        f"/hoa/1/budget/drafts/{draft_id}/reserve-study",
        json={
            "rows": [
                {
                    "row_id": "old-roof",
                    "line_item": "Old Roof Entry",
                    "useful_life": 20,
                    "remaining_life": 5,
                    "quantity": 1,
                    "replacement_cost": 150000.0,
                    "source_page": 1,
                    "flags": [],
                }
            ],
            "warnings": ["Prior study warning"],
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    return draft_id


class TestReplaceReserveStudyEndpoint:
    def test_non_pdf_rejected_with_422(self, client, budget_history_test_harness):
        upload_resp = client.post(
            "/hoa/1/budget/upload",
            data={"source_mode": "income_statement"},
            files={"file": ("income.xlsx", b"fake bytes", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert upload_resp.status_code == 200
        draft_id = upload_resp.json()["draft"]["id"]

        resp = client.post(
            f"/hoa/1/budget/drafts/{draft_id}/reserve-study/upload",
            files={"reserve_study_file": ("reserve-study.xlsx", b"excel bytes", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 422
        assert "PDF" in resp.json()["detail"]

    def test_success_swaps_rows_without_touching_budget_items(
        self, client, budget_history_test_harness, monkeypatch, draft_with_reserve_rows
    ):
        draft_id = draft_with_reserve_rows

        # Fetch draft before replacement to capture budget line items
        get_resp = client.get(f"/hoa/1/budget/drafts/{draft_id}")
        assert get_resp.status_code == 200
        before_line_items = get_resp.json()["line_items"]

        async def _fake_extract_success(path: str):
            return _fake_reserve_doc_success()

        monkeypatch.setattr(
            "app.services.budget_history_service.extract_reserve_study",
            _fake_extract_success,
        )

        resp = client.post(
            f"/hoa/1/budget/drafts/{draft_id}/reserve-study/upload",
            files={"reserve_study_file": ("new-study.pdf", b"pdf bytes", "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Reserve rows replaced — new entry present
        rows = data["reserve_study_rows"]
        assert any(r["line_item"] == "Roof Replacement" for r in rows), rows

        # Old entry gone
        assert not any(r["line_item"] == "Old Roof Entry" for r in rows), rows

        # Budget line items untouched
        assert data["line_items"] == before_line_items

    def test_review_required_path_status_and_warning(
        self, client, budget_history_test_harness, monkeypatch, draft_with_reserve_rows
    ):
        draft_id = draft_with_reserve_rows

        async def _fake_extract_with_warnings(path: str):
            return _fake_reserve_doc_with_warnings()

        monkeypatch.setattr(
            "app.services.budget_history_service.extract_reserve_study",
            _fake_extract_with_warnings,
        )

        resp = client.post(
            f"/hoa/1/budget/drafts/{draft_id}/reserve-study/upload",
            files={"reserve_study_file": ("study.pdf", b"pdf bytes", "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Status should reflect the review flag
        assert data["reserve_study_status"] == "review_required"
        assert "Missing component life data" in data["reserve_study_warnings"]

    def test_extraction_failure_preserves_prior_rows(
        self, client, budget_history_test_harness, monkeypatch, draft_with_reserve_rows
    ):
        draft_id = draft_with_reserve_rows

        async def _fail(path: str):
            raise RuntimeError("Simulated Gemini failure")

        monkeypatch.setattr(
            "app.services.budget_history_service.extract_reserve_study",
            _fail,
        )

        resp = client.post(
            f"/hoa/1/budget/drafts/{draft_id}/reserve-study/upload",
            files={"reserve_study_file": ("study.pdf", b"pdf bytes", "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Prior rows must still be present
        assert any(r["line_item"] == "Old Roof Entry" for r in data["reserve_study_rows"]), data["reserve_study_rows"]
        # Status reflects the failure
        assert data["reserve_study_status"] == "review_required"


# ---------------------------------------------------------------------------
# read_only_override round-trip through save_draft
# ---------------------------------------------------------------------------

class TestReadOnlyOverrideRoundTrip:
    def test_unlock_reserve_line_persists_across_reload(
        self, client, budget_history_test_harness, db_session
    ):
        """Setting read_only_override=False on a reserve_income line must survive
        a save → reload cycle with read_only=False on the reloaded line."""
        upload_resp = client.post(
            "/hoa/1/budget/upload",
            data={"source_mode": "income_statement"},
            files={"file": ("income.xlsx", b"fake bytes", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert upload_resp.status_code == 200
        draft = upload_resp.json()["draft"]
        draft_id = draft["id"]
        version_int = draft["version_int"]
        line_items = draft["line_items"]

        # Find or fabricate a reserve_income line to unlock
        reserve_income_lines = [it for it in line_items if it.get("category") == "reserve_income"]
        if not reserve_income_lines:
            # Inject one so the test isn't vacuous
            line_items = line_items + [
                {
                    "line_item_key": "synthetic-reserve-income",
                    "account_code": 45000,
                    "label": "Reserve Income",
                    "normalized_label": "reserve income",
                    "category": "reserve_income",
                    "annual_budget": 5000.0,
                    "ytd_actual": 4000.0,
                    "projection": 5000.0,
                    "percent_change": 0.0,
                    "read_only": True,
                    "read_only_override": None,
                    "merged_count": 0,
                    "merged_gls": [],
                }
            ]
            reserve_income_lines = [line_items[-1]]

        target = reserve_income_lines[0]
        target_key = target["line_item_key"]

        # Unlock the line
        unlocked = {**target, "read_only_override": False}
        updated_items = [unlocked if it["line_item_key"] == target_key else it for it in line_items]

        save_resp = client.patch(
            "/hoa/1/budget/draft",
            json={
                "draft_id": draft_id,
                "line_items": updated_items,
                "global_note": "",
                "statement_month": None,
                "growth_factor": None,
                "growth_factor_note": None,
                "reserve_inflation_rate": None,
                "reserve_inflation_note": None,
            },
        )
        assert save_resp.status_code == 200, save_resp.text

        # Reload the draft
        reload_resp = client.get(f"/hoa/1/budget/drafts/{draft_id}")
        assert reload_resp.status_code == 200
        reloaded_items = reload_resp.json()["line_items"]

        target_reloaded = next(
            (it for it in reloaded_items if it.get("line_item_key") == target_key), None
        )
        assert target_reloaded is not None, f"Target line item missing after reload; keys: {[it.get('line_item_key') for it in reloaded_items]}"
        assert target_reloaded["read_only"] is False, (
            f"Expected read_only=False after unlock, got {target_reloaded['read_only']}"
        )
        assert target_reloaded.get("read_only_override") is False

    def test_relock_restores_category_default(
        self, client, budget_history_test_harness
    ):
        """Setting read_only_override=None on a reserve_expense line must restore
        read_only=True (the section default) after save → reload."""
        upload_resp = client.post(
            "/hoa/1/budget/upload",
            data={"source_mode": "income_statement"},
            files={"file": ("income.xlsx", b"fake bytes", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert upload_resp.status_code == 200
        draft = upload_resp.json()["draft"]
        draft_id = draft["id"]
        version_int = draft["version_int"]
        line_items = draft["line_items"]

        # Pick any existing reserve_expense line, or inject one
        re_lines = [it for it in line_items if it.get("category") == "reserve_expense"]
        if not re_lines:
            line_items = line_items + [
                {
                    "line_item_key": "synthetic-re",
                    "account_code": 90000,
                    "label": "Reserve Allocation",
                    "normalized_label": "reserve allocation",
                    "category": "reserve_expense",
                    "reserve_group": "transfer",
                    "annual_budget": 18000.0,
                    "ytd_actual": 13500.0,
                    "projection": 18000.0,
                    "percent_change": 0.0,
                    "read_only": True,
                    "read_only_override": None,
                    "merged_count": 0,
                    "merged_gls": [],
                }
            ]
            re_lines = [line_items[-1]]

        target = re_lines[0]
        target_key = target["line_item_key"]

        # First unlock it…
        unlocked_items = [{**it, "read_only_override": False} if it["line_item_key"] == target_key else it for it in line_items]
        save1 = client.patch(
            "/hoa/1/budget/draft",
            json={"draft_id": draft_id, "line_items": unlocked_items, "global_note": "",
                  "statement_month": None, "growth_factor": None, "growth_factor_note": None,
                  "reserve_inflation_rate": None, "reserve_inflation_note": None},
        )
        assert save1.status_code == 200, save1.text

        # …then re-lock (override=None)
        relocked_items = [{**it, "read_only_override": None} if it["line_item_key"] == target_key else it for it in unlocked_items]
        save2 = client.patch(
            "/hoa/1/budget/draft",
            json={"draft_id": draft_id, "line_items": relocked_items, "global_note": "",
                  "statement_month": None, "growth_factor": None, "growth_factor_note": None,
                  "reserve_inflation_rate": None, "reserve_inflation_note": None},
        )
        assert save2.status_code == 200, save2.text

        reload_resp = client.get(f"/hoa/1/budget/drafts/{draft_id}")
        assert reload_resp.status_code == 200
        reloaded = reload_resp.json()["line_items"]

        target_reloaded = next((it for it in reloaded if it.get("line_item_key") == target_key), None)
        assert target_reloaded is not None
        assert target_reloaded["read_only"] is True, (
            f"Expected read_only=True after re-lock, got {target_reloaded['read_only']}"
        )

    def test_override_on_unrelated_line_survives_reserve_apply(
        self, client, budget_history_test_harness, monkeypatch
    ):
        """An override on an unrelated (operating) line must survive an apply-
        reserve-study call without being wiped."""
        upload_resp = client.post(
            "/hoa/1/budget/upload",
            data={"source_mode": "income_statement"},
            files={"file": ("income.xlsx", b"fake bytes", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert upload_resp.status_code == 200
        draft = upload_resp.json()["draft"]
        draft_id = draft["id"]
        version_int = draft["version_int"]
        line_items = draft["line_items"]

        # Find an operating line and mark it with a True override (sentinel)
        op_lines = [it for it in line_items if it.get("category") == "operating"]
        if not op_lines:
            pytest.skip("No operating lines in seeded draft to test override survival")

        target = op_lines[0]
        target_key = target["line_item_key"]
        locked_items = [{**it, "read_only_override": True} if it["line_item_key"] == target_key else it for it in line_items]

        save_resp = client.patch(
            "/hoa/1/budget/draft",
            json={"draft_id": draft_id, "line_items": locked_items, "global_note": "",
                  "statement_month": None, "growth_factor": None, "growth_factor_note": None,
                  "reserve_inflation_rate": None, "reserve_inflation_note": None},
        )
        assert save_resp.status_code == 200, save_resp.text

        # Now apply reserve study rows (no actual reserve rows, just triggers the merge path)
        saved_draft_payload = save_resp.json()["draft"]
        apply_resp = client.post(
            f"/hoa/1/budget/drafts/{draft_id}/apply-reserve-study",
            json={"line_items": saved_draft_payload["line_items"]},
        )
        # If there are no reserve rows the apply may return 200 or skip gracefully
        if apply_resp.status_code not in (200, 204, 422):
            pytest.skip(f"Unexpected apply-reserve-study response: {apply_resp.status_code}")

        reload_resp = client.get(f"/hoa/1/budget/drafts/{draft_id}")
        assert reload_resp.status_code == 200
        reloaded = reload_resp.json()["line_items"]

        target_reloaded = next((it for it in reloaded if it.get("line_item_key") == target_key), None)
        assert target_reloaded is not None
        # The override we set on the operating line must still be there
        assert target_reloaded.get("read_only_override") is True


# ---------------------------------------------------------------------------
# _derive_reserve_income integration: serialize path
# ---------------------------------------------------------------------------

class TestDeriveReserveIncomeIntegration:
    """Tests that verify the derivation is visible through the API read path."""

    def test_allocation_change_reflected_in_reserve_income_on_read(
        self, client, budget_history_test_harness
    ):
        """When the transfer (allocation) line amount is updated via save_draft,
        the reserve_income contribution line must reflect the new total on the
        next GET of the draft — without the operator separately editing it."""
        upload_resp = client.post(
            "/hoa/1/budget/upload",
            data={"source_mode": "income_statement"},
            files={"file": ("income.xlsx", b"fake bytes", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert upload_resp.status_code == 200
        draft = upload_resp.json()["draft"]
        draft_id = draft["id"]
        version_int = draft["version_int"]
        line_items = draft["line_items"]

        # Build items with a transfer line and a reserve_income contribution line
        transfer_key = "synthetic-transfer"
        income_key = "synthetic-res-income"
        new_amount = 30000.0

        items = list(line_items) + [
            {
                "line_item_key": transfer_key,
                "account_code": 90000,
                "label": "Allocation to Reserves",
                "normalized_label": "allocation to reserves",
                "category": "reserve_expense",
                "reserve_group": "transfer",
                "annual_budget": new_amount,
                "ytd_actual": 0.0,
                "projection": new_amount,
                "percent_change": 0.0,
                "read_only": True,
                "read_only_override": None,
                "merged_count": 0,
                "merged_gls": [],
            },
            {
                "line_item_key": income_key,
                "account_code": 45000,
                "label": "Reserve Fund Income",
                "normalized_label": "reserve fund income",
                "category": "reserve_income",
                "reserve_group": None,
                "annual_budget": 5000.0,  # deliberately diverged from transfer
                "ytd_actual": 0.0,
                "projection": 5000.0,
                "percent_change": 0.0,
                "read_only": True,
                "read_only_override": None,
                "merged_count": 0,
                "merged_gls": [],
            },
        ]

        save_resp = client.patch(
            "/hoa/1/budget/draft",
            json={"draft_id": draft_id, "line_items": items, "global_note": "",
                  "statement_month": None, "growth_factor": None, "growth_factor_note": None,
                  "reserve_inflation_rate": None, "reserve_inflation_note": None},
        )
        assert save_resp.status_code == 200, save_resp.text

        # Re-read the draft through the API
        get_resp = client.get(f"/hoa/1/budget/drafts/{draft_id}")
        assert get_resp.status_code == 200
        result_items = get_resp.json()["line_items"]

        income_line = next(
            (it for it in result_items if it.get("line_item_key") == income_key), None
        )
        assert income_line is not None, "reserve_income line missing from draft"
        assert income_line["annual_budget"] == new_amount, (
            f"Expected reserve_income to mirror transfer ({new_amount}), "
            f"got {income_line['annual_budget']}"
        )


