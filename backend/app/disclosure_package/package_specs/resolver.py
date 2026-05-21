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


# setup_type → assessment-schedule template path.
# Spec entries reference templates by relative path under the loader's
# subdirectory (``templates/standard/``). The compiler should call
# ``template_for_setup_type`` when it needs to know which template would
# be used for a given setup. All setup types now render through the
# Universal Assessment Matrix. The legacy fixed/grouped/per_unit templates
# remain on disk as a rollback path while matrix regression coverage grows.
_SETUP_TYPE_TEMPLATE_MAP = {
    "fixed": "assessment_schedule/universal.html",
    "grouped": "assessment_schedule/universal.html",
    "per_unit": "assessment_schedule/universal.html",
}


def template_for_setup_type(setup_type: str) -> str:
    """Return the assessment-schedule template path for a setup_type.

    Raises:
        ValueError: when ``setup_type`` is unknown.
    """
    if setup_type not in _SETUP_TYPE_TEMPLATE_MAP:
        raise ValueError(
            f"Unknown setup_type {setup_type!r}; expected one of "
            f"{sorted(_SETUP_TYPE_TEMPLATE_MAP)}"
        )
    return _SETUP_TYPE_TEMPLATE_MAP[setup_type]


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

        return STANDARD_PACKAGE_SPEC.model_copy(
            update={"hoa_id": property_id, "fiscal_year": fiscal_year}
        )
    finally:
        if close_after:
            connection.close()
