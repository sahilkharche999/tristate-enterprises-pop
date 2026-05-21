"""Tenant scoping constants (Phase 5.8 of dre-driven-assessment-engine).

v1 is single-tenant: every row defaults to ``tenant_id=1``. The future
multi-tenant rollout will replace ``CURRENT_TENANT_ID`` with a request-
scoped value (from auth middleware) and start filtering queries on
tables that have a ``tenant_id`` column.

Tables with ``tenant_id``:

- ``properties``
- ``hoa_settings``
- ``dre_documents``
- ``assessment_setups``
- ``annual_packages``
- ``appendix_documents``

Helper :func:`apply_tenant_filter` adds the ``WHERE tenant_id = ?``
clause to a SQL fragment, matching the call-site idiom (existing
queries can wrap their WHERE clause through this helper before
execution to opt into tenant filtering without retyping the literal).
"""

from __future__ import annotations

from typing import Optional


CURRENT_TENANT_ID: int = 1
"""Hardcoded tenant id for v1.

Production callers should read this constant rather than literal ``1``
so the eventual transition to a request-scoped lookup is a one-line
change. Tests may monkeypatch the module attribute to verify
filter behavior.
"""


TENANT_SCOPED_TABLES: frozenset[str] = frozenset(
    [
        "properties",
        "hoa_settings",
        "dre_documents",
        "assessment_setups",
        "annual_packages",
        "appendix_documents",
    ]
)


def current_tenant_id() -> int:
    """Return the current tenant id. Indirection so future multi-tenant
    rollout can replace the constant with a context-local read without
    touching every call site.
    """
    return CURRENT_TENANT_ID


def apply_tenant_filter(
    base_where: Optional[str], alias: Optional[str] = None
) -> tuple[str, tuple[int]]:
    """Append a ``tenant_id = ?`` clause to a WHERE fragment.

    Returns ``(where_sql, params_tuple)`` so callers can spread params
    directly into a parameterized query.

    Example::

        where_sql, params = apply_tenant_filter("status = 'active'")
        cursor.execute(f"SELECT id FROM properties WHERE {where_sql}",
                       ("active_status_value_if_needed", *params))

    Note: most callers don't need the helper — they just splice
    ``AND tenant_id = ?`` and pass ``current_tenant_id()`` directly.
    The helper exists for the cleanup-pass to make the convention
    explicit and grep-able.
    """
    clause = f"{alias + '.' if alias else ''}tenant_id = ?"
    if not base_where:
        return clause, (current_tenant_id(),)
    return f"({base_where}) AND {clause}", (current_tenant_id(),)


__all__ = [
    "CURRENT_TENANT_ID",
    "TENANT_SCOPED_TABLES",
    "apply_tenant_filter",
    "current_tenant_id",
]
