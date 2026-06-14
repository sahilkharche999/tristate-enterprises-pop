"""AnnualPackage lifecycle service (Phase 4.8 + 4.9).

State machine for ``annual_packages`` rows:

    draft → approved → rendered → finalized

Plus ``preflight_failed`` (from the draft side) and the regeneration
flow where a finalized package spawns a new draft row with
``regen_of_package_id`` set to the original.

The finalize transition is the critical one: it atomically freezes
all four snapshot JSON columns (assessment_setup, budget, reserve,
appendix_manifest) plus ``finalized_at`` so a later re-render uses
the same inputs even if live state has drifted. Uses
``disclosure_package.snapshots.freeze_package_snapshots`` for the
deterministic serialization.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel

from app.assessment_mode import (
    ASSESSMENT_MODE_VARIABLE,
    AssessmentMode,
    PackageImpact,
    normalize_assessment_mode,
    package_impact_for_mode_drift,
)
from app.disclosure_package.snapshots import freeze_package_snapshots


PackageStatus = Literal[
    "draft", "preflight_failed", "approved", "rendered", "finalized"
]


class AnnualPackageResponse(BaseModel):
    """JSON shape returned by the annual-package endpoints."""

    package_id: int
    property_id: int
    assessment_setup_id: Optional[int]
    budget_year: int
    fiscal_year: int
    status: PackageStatus
    approved_assessment_revenue_annual: Optional[Decimal]
    approved_by: Optional[str]
    approved_at: Optional[str]
    finalized_at: Optional[str]
    regen_of_package_id: Optional[int]
    version_int: int
    assessment_mode: AssessmentMode = ASSESSMENT_MODE_VARIABLE
    live_assessment_mode: AssessmentMode = ASSESSMENT_MODE_VARIABLE
    package_impact: PackageImpact = "none"
    package_impact_reason: Optional[str] = None


class AnnualPackageNotFound(LookupError):
    """Raised when the package isn't found for the property."""


class InvalidPackageStateTransition(RuntimeError):
    """Raised when a state transition isn't allowed (e.g., finalizing a
    draft, approving a finalized package).
    """


class PackageVersionMismatch(RuntimeError):
    """Raised when the supplied If-Match version doesn't match the
    row's current ``version_int``. Endpoints map this to HTTP 409.
    """

    def __init__(self, *, package_id: int, expected: int, actual: int) -> None:
        self.package_id = package_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"annual_packages id={package_id} version mismatch "
            f"(expected={expected}, actual={actual})"
        )


class FinalizeBlockedByPreflight(RuntimeError):
    """Raised when finalize is called against a package whose preflight
    inputs return blocking errors (task 153). Endpoint maps to HTTP 422
    with the list of field paths so the operator can fix them.
    """

    def __init__(self, *, package_id: int, field_paths: list[str]) -> None:
        self.package_id = package_id
        self.field_paths = field_paths
        super().__init__(
            f"finalize blocked: package_id={package_id} has "
            f"{len(field_paths)} preflight error(s): {field_paths}"
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


_SELECT_FIELDS = (
    "id, property_id, assessment_setup_id, budget_year, fiscal_year, "
    "status, approved_assessment_revenue_annual, approved_by, "
    "approved_at, finalized_at, regen_of_package_id, version_int, assessment_mode"
)


def _property_assessment_mode(
    *,
    property_id: int,
    connection: sqlite3.Connection,
) -> AssessmentMode:
    row = connection.execute(
        "SELECT assessment_mode FROM properties WHERE id = ?",
        (property_id,),
    ).fetchone()
    return normalize_assessment_mode(row[0] if row else None)


def _is_latest_for_fiscal_year(
    *,
    property_id: int,
    package_id: int,
    fiscal_year: int,
    connection: sqlite3.Connection,
) -> bool:
    latest_row = connection.execute(
        """
        SELECT id
          FROM annual_packages
         WHERE property_id = ? AND fiscal_year = ?
         ORDER BY id DESC
         LIMIT 1
        """,
        (property_id, fiscal_year),
    ).fetchone()
    return latest_row is not None and int(latest_row[0]) == package_id


def _row_to_response(row: tuple, *, connection: sqlite3.Connection) -> AnnualPackageResponse:
    approved_revenue = row[6]
    property_id = int(row[1])
    fiscal_year = int(row[4])
    assessment_mode = normalize_assessment_mode(row[12] if len(row) > 12 else None)
    live_assessment_mode = _property_assessment_mode(
        property_id=property_id,
        connection=connection,
    )
    package_impact, package_impact_reason = package_impact_for_mode_drift(
        status=str(row[5]),
        package_assessment_mode=assessment_mode,
        live_assessment_mode=live_assessment_mode,
        is_latest_for_fiscal_year=_is_latest_for_fiscal_year(
            property_id=property_id,
            package_id=int(row[0]),
            fiscal_year=fiscal_year,
            connection=connection,
        ),
    )
    return AnnualPackageResponse(
        package_id=row[0],
        property_id=property_id,
        assessment_setup_id=row[2],
        budget_year=row[3],
        fiscal_year=fiscal_year,
        status=row[5],
        approved_assessment_revenue_annual=(
            Decimal(str(approved_revenue)) if approved_revenue is not None else None
        ),
        approved_by=row[7],
        approved_at=row[8],
        finalized_at=row[9],
        regen_of_package_id=row[10],
        version_int=row[11],
        assessment_mode=assessment_mode,
        live_assessment_mode=live_assessment_mode,
        package_impact=package_impact,
        package_impact_reason=package_impact_reason,
    )


def _fetch_package(
    *, property_id: int, package_id: int, connection: sqlite3.Connection
) -> AnnualPackageResponse:
    row = connection.execute(
        f"SELECT {_SELECT_FIELDS} FROM annual_packages "
        "WHERE id = ? AND property_id = ?",
        (package_id, property_id),
    ).fetchone()
    if row is None:
        raise AnnualPackageNotFound(
            f"package_id={package_id} not found for property_id={property_id}"
        )
    return _row_to_response(row, connection=connection)


def create_annual_package(
    *,
    property_id: int,
    budget_year: int,
    fiscal_year: int,
    assessment_setup_id: Optional[int] = None,
    regen_of_package_id: Optional[int] = None,
    connection: sqlite3.Connection,
) -> AnnualPackageResponse:
    """Create a new ``annual_packages`` row in ``status='draft'``.

    ``regen_of_package_id`` links a regeneration to its predecessor;
    the new row gets its own preflight/approval/finalize cycle. Pass
    ``None`` for the first-time draft of a (property, fiscal_year).
    """
    assessment_mode = _property_assessment_mode(
        property_id=property_id,
        connection=connection,
    )
    cur = connection.execute(
        """
        INSERT INTO annual_packages (
            property_id, budget_year, fiscal_year,
            assessment_setup_id, regen_of_package_id, assessment_mode, status
        ) VALUES (?, ?, ?, ?, ?, ?, 'draft')
        """,
        (
            property_id, budget_year, fiscal_year,
            assessment_setup_id, regen_of_package_id, assessment_mode,
        ),
    )
    package_id = cur.lastrowid
    if package_id is None:
        raise RuntimeError("sqlite did not return a lastrowid for annual_packages")
    connection.commit()
    return _fetch_package(
        property_id=property_id, package_id=package_id, connection=connection,
    )


def approve_annual_package(
    *,
    property_id: int,
    package_id: int,
    approved_assessment_revenue_annual: Decimal,
    approved_by: str,
    connection: sqlite3.Connection,
    expected_version: Optional[int] = None,
) -> AnnualPackageResponse:
    """Transition a draft package to ``approved`` and freeze the
    operator-approved revenue target the engine reconciles against.

    Pass ``expected_version`` (from the If-Match header) to enforce
    optimistic concurrency: the call fails with
    :class:`PackageVersionMismatch` if the row was modified by another
    client since the caller's read.
    """
    current = _fetch_package(
        property_id=property_id, package_id=package_id, connection=connection,
    )
    if expected_version is not None and current.version_int != expected_version:
        raise PackageVersionMismatch(
            package_id=package_id,
            expected=expected_version,
            actual=current.version_int,
        )
    if current.status not in ("draft", "preflight_failed"):
        raise InvalidPackageStateTransition(
            f"Cannot approve package_id={package_id} from status={current.status!r}; "
            "approve requires draft or preflight_failed."
        )
    live_assessment_mode = _property_assessment_mode(
        property_id=property_id,
        connection=connection,
    )
    connection.execute(
        """
        UPDATE annual_packages
           SET status = 'approved',
               assessment_mode = ?,
               approved_assessment_revenue_annual = ?,
               approved_by = ?,
               approved_at = ?,
               version_int = version_int + 1
         WHERE id = ?
        """,
        (
            live_assessment_mode,
            str(approved_assessment_revenue_annual),
            approved_by,
            _now_iso(),
            package_id,
        ),
    )
    connection.commit()
    return _fetch_package(
        property_id=property_id, package_id=package_id, connection=connection,
    )


def finalize_annual_package(
    *,
    property_id: int,
    package_id: int,
    assessment_setup: Any,
    budget: Any,
    reserve: Any,
    appendix_manifest: Any,
    connection: sqlite3.Connection,
    expected_version: Optional[int] = None,
) -> AnnualPackageResponse:
    """Transition an approved/rendered package to ``finalized`` and
    freeze all four snapshot JSONs atomically.

    Pre-condition: status must be ``approved`` or ``rendered``. After
    finalization the package is immutable — re-renders MUST load from
    the snapshot JSONs, not live state.

    Pass ``expected_version`` (from the If-Match header) for
    optimistic-lock enforcement.
    """
    current = _fetch_package(
        property_id=property_id, package_id=package_id, connection=connection,
    )
    if expected_version is not None and current.version_int != expected_version:
        raise PackageVersionMismatch(
            package_id=package_id,
            expected=expected_version,
            actual=current.version_int,
        )
    if current.status not in ("approved", "rendered"):
        raise InvalidPackageStateTransition(
            f"Cannot finalize package_id={package_id} from status={current.status!r}; "
            "finalize requires approved or rendered."
        )

    live_assessment_mode = _property_assessment_mode(
        property_id=property_id,
        connection=connection,
    )
    connection.execute(
        """
        UPDATE annual_packages
           SET assessment_mode = ?
         WHERE id = ?
        """,
        (live_assessment_mode, package_id),
    )

    freeze_package_snapshots(
        package_id=package_id,
        assessment_setup=assessment_setup,
        budget=budget,
        reserve=reserve,
        appendix_manifest=appendix_manifest,
        assessment_mode=live_assessment_mode,
        connection=connection,
    )
    return _fetch_package(
        property_id=property_id, package_id=package_id, connection=connection,
    )


def list_annual_packages(
    *,
    property_id: int,
    connection: sqlite3.Connection,
) -> list[AnnualPackageResponse]:
    """Return every AnnualPackage row for an HOA, newest first."""
    rows = connection.execute(
        f"SELECT {_SELECT_FIELDS} FROM annual_packages "
        "WHERE property_id = ? "
        "ORDER BY fiscal_year DESC, id DESC",
        (property_id,),
    ).fetchall()
    return [_row_to_response(r, connection=connection) for r in rows]


def get_annual_package(
    *,
    property_id: int,
    package_id: int,
    connection: sqlite3.Connection,
) -> AnnualPackageResponse:
    """Read one AnnualPackage row."""
    return _fetch_package(
        property_id=property_id, package_id=package_id, connection=connection,
    )
