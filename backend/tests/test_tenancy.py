"""Tenant filter helper tests (Phase 5.8 task 177)."""
from __future__ import annotations

from app.tenancy import (
    CURRENT_TENANT_ID,
    TENANT_SCOPED_TABLES,
    apply_tenant_filter,
    current_tenant_id,
)


def test_current_tenant_id_returns_constant():
    assert current_tenant_id() == CURRENT_TENANT_ID
    assert CURRENT_TENANT_ID == 1


def test_tenant_scoped_tables_lists_required_set():
    # If a new tenant_id column is added, update this set + the schema.
    expected = {
        "properties",
        "hoa_settings",
        "dre_documents",
        "assessment_setups",
        "annual_packages",
        "appendix_documents",
    }
    assert TENANT_SCOPED_TABLES == expected


def test_apply_tenant_filter_with_existing_where():
    where, params = apply_tenant_filter("status = 'active'")
    assert where == "(status = 'active') AND tenant_id = ?"
    assert params == (1,)


def test_apply_tenant_filter_with_empty_where():
    where, params = apply_tenant_filter(None)
    assert where == "tenant_id = ?"
    assert params == (1,)


def test_apply_tenant_filter_with_table_alias():
    where, params = apply_tenant_filter("p.status = 'active'", alias="p")
    assert where == "(p.status = 'active') AND p.tenant_id = ?"


def test_apply_tenant_filter_respects_monkeypatched_constant(monkeypatch):
    """Tests can swap tenant id to verify multi-tenant behavior."""
    import app.tenancy as tenancy

    monkeypatch.setattr(tenancy, "CURRENT_TENANT_ID", 42)
    where, params = apply_tenant_filter("status = 'x'")
    assert params == (42,)
