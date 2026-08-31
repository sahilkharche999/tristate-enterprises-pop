"""DB-backed resolution of PackageSpec for an HOA + fiscal year.

The DRE-driven engine uses one universal template chain
(``STANDARD_PACKAGE_SPEC``) for every HOA. Per-HOA static data —
HOA name, management company, CPA firm, monthly assessments, etc. —
comes from the ``properties`` and ``hoa_settings`` tables, never from
code-embedded literals. This function returns the spec with the
caller's ``property_id`` + ``fiscal_year`` stamped into the sentinel
fields.

``UnsupportedHOAError`` is now only raised when the property row
itself is missing (the caller passed an unknown ``property_id``).
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from ..schemas import PackageSpec
from ..section_order import apply_to_spec, load_saved_lists
from .standard import STANDARD_PACKAGE_SPEC


class UnsupportedHOAError(Exception):
    """Raised when no PackageSpec can be resolved for a property.

    With the DRE-driven engine the only remaining cause is a missing
    properties row — every HOA shares ``STANDARD_PACKAGE_SPEC``.
    """

    def __init__(self, property_id: int, hoa_name: Optional[str] = None) -> None:
        self.property_id = property_id
        self.hoa_name = hoa_name
        super().__init__(
            f"No PackageSpec for property_id={property_id}"
            + (f" ({hoa_name!r})" if hoa_name else "")
            + "."
        )


def resolve(
    property_id: int,
    fiscal_year: int,
    *,
    connection: Optional[sqlite3.Connection] = None,
) -> PackageSpec:
    """Resolve the PackageSpec for one (property, year).

    Verifies the property exists, then returns ``STANDARD_PACKAGE_SPEC``
    with ``hoa_id`` and ``fiscal_year`` stamped in. All other per-HOA
    values flow from DB tables at compile time.

    ``connection`` is an optional pre-opened SQLite connection; when
    omitted the function pulls one from the SQLAlchemy engine. Tests
    can inject a temp connection to avoid the real DB.
    """
    close_after = False
    if connection is None:
        from app.ai_implementation.db.session import engine

        connection = engine.raw_connection()
        close_after = True

    try:
        row = connection.execute(
            "SELECT name FROM properties WHERE id = ?",
            (property_id,),
        ).fetchone()
        if row is None:
            raise UnsupportedHOAError(property_id)

        saved_order, hidden = load_saved_lists(connection)
        spec = STANDARD_PACKAGE_SPEC.model_copy(
            update={"hoa_id": property_id, "fiscal_year": fiscal_year}
        )
        return apply_to_spec(spec, saved_order=saved_order, hidden=hidden)
    finally:
        if close_after:
            connection.close()
