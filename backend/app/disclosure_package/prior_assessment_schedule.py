"""Prior-year assessment schedule resolve + year-1 seed helpers.

Resolve order (design D2):
  1. Finalized annual package for fiscal_year - 1 → compile_context.assessment_matrix
  2. Operator-confirmed seed on properties (JSON + year)
  3. None

Does not invent multi-unit schedules from monthly_assessment_per_unit_prior.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from .assessment_schedule_matrix import (
    AssessmentScheduleMatrix,
    ColumnDescriptor,
    MethodSummary,
    UnitAssessmentRow,
)

logger = logging.getLogger(__name__)

# properties columns (brownfield) for year-1 seed
SEED_JSON_COLUMN = "prior_assessment_schedule_json"
SEED_YEAR_COLUMN = "prior_assessment_schedule_year"


def _as_connection(connection: Any) -> sqlite3.Connection:
    if isinstance(connection, sqlite3.Connection):
        return connection
    # SQLAlchemy Connection → DBAPI
    raw = getattr(connection, "connection", None)
    if raw is not None and isinstance(raw, sqlite3.Connection):
        return raw
    if hasattr(connection, "cursor"):
        return connection  # type: ignore[return-value]
    raise TypeError(f"Unsupported connection type: {type(connection)!r}")


def load_finalized_assessment_matrix(
    connection: Any,
    *,
    property_id: int,
    fiscal_year: int,
) -> Optional[AssessmentScheduleMatrix]:
    """Return assessment_matrix from the latest finalized package for FY."""
    conn = _as_connection(connection)
    row = conn.execute(
        """
        SELECT compile_context_snapshot_json
        FROM annual_packages
        WHERE property_id = ?
          AND fiscal_year = ?
          AND status = 'finalized'
          AND compile_context_snapshot_json IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (property_id, fiscal_year),
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        context = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        logger.warning(
            "prior schedule: invalid compile_context JSON for property=%s fy=%s",
            property_id,
            fiscal_year,
        )
        return None
    matrix_payload = context.get("assessment_matrix")
    if not matrix_payload:
        return None
    try:
        return AssessmentScheduleMatrix.model_validate(matrix_payload)
    except Exception:
        logger.exception(
            "prior schedule: failed to validate matrix for property=%s fy=%s",
            property_id,
            fiscal_year,
        )
        return None


def load_prior_seed(
    connection: Any,
    *,
    property_id: int,
) -> Optional[tuple[int, list[dict[str, Any]]]]:
    """Return (seed_year, rows) from properties seed columns, or None."""
    conn = _as_connection(connection)
    # Columns may not exist until brownfield migration runs.
    try:
        row = conn.execute(
            f"""
            SELECT {SEED_YEAR_COLUMN}, {SEED_JSON_COLUMN}
            FROM properties
            WHERE id = ?
            """,
            (property_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    year_raw, json_raw = row[0], row[1]
    if year_raw is None or not json_raw:
        return None
    try:
        year = int(year_raw)
        payload = json.loads(json_raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        return None
    return year, rows


def save_prior_seed(
    connection: Any,
    *,
    property_id: int,
    fiscal_year: int,
    rows: list[dict[str, Any]],
) -> None:
    """Persist operator-confirmed prior schedule seed on properties."""
    conn = _as_connection(connection)
    payload = json.dumps({"rows": rows, "fiscal_year": fiscal_year})
    conn.execute(
        f"""
        UPDATE properties
        SET {SEED_YEAR_COLUMN} = ?, {SEED_JSON_COLUMN} = ?
        WHERE id = ?
        """,
        (int(fiscal_year), payload, property_id),
    )
    conn.commit()


def clear_prior_seed(connection: Any, *, property_id: int) -> None:
    conn = _as_connection(connection)
    try:
        conn.execute(
            f"""
            UPDATE properties
            SET {SEED_YEAR_COLUMN} = NULL, {SEED_JSON_COLUMN} = NULL
            WHERE id = ?
            """,
            (property_id,),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def matrix_from_seed_rows(
    *,
    hoa_name: str,
    fiscal_year: int,
    rows: list[dict[str, Any]],
) -> AssessmentScheduleMatrix:
    """Build a unit-grain matrix for prior-year display from simple seed rows.

    Expected row keys: recipient_label (or label/unit), monthly (or monthly_assessment),
    optional percent_of_total / ownership_percent.
    """
    unit_rows: list[UnitAssessmentRow] = []
    has_percent = False
    for raw in rows:
        label = (
            raw.get("recipient_label")
            or raw.get("label")
            or raw.get("unit")
            or raw.get("recipient_key")
            or ""
        )
        label = str(label).strip()
        if not label:
            continue
        monthly_raw = raw.get("monthly", raw.get("monthly_assessment", raw.get("amount")))
        try:
            monthly = Decimal(str(monthly_raw)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            monthly = Decimal("0.00")
        basis: dict[str, Any] = {}
        pct_raw = raw.get("percent_of_total", raw.get("ownership_percent", raw.get("percent")))
        if pct_raw is not None and pct_raw != "":
            try:
                basis["percent_of_total"] = Decimal(str(pct_raw))
                has_percent = True
            except (InvalidOperation, TypeError, ValueError):
                pass
        unit_rows.append(
            UnitAssessmentRow(
                recipient_label=label,
                basis_values=basis,
                component_values_monthly={},
                total_monthly_assessment=monthly,
                annual_total=(monthly * Decimal("12")).quantize(Decimal("0.01")),
            )
        )

    basis_columns: list[ColumnDescriptor] = []
    if has_percent:
        basis_columns.append(
            ColumnDescriptor(
                key="percent_of_total",
                label="Percentage of Undivided Interest",
                kind="basis",
                value_type="percent",
            )
        )
    total_columns = [
        ColumnDescriptor(
            key="total_monthly_assessment",
            label=f"{fiscal_year} Monthly Assessment",
            kind="total",
            value_type="currency",
        ),
        ColumnDescriptor(
            key="annual_total",
            label="Annual Assessment",
            kind="total",
            value_type="currency",
        ),
    ]
    return AssessmentScheduleMatrix(
        title=f"{hoa_name}\n{fiscal_year} Assessments Per Unit Per Month",
        hoa={"name": hoa_name},
        fiscal_year=fiscal_year,
        recipient_grain="unit",
        method_summary=MethodSummary(
            assessment_method="Prior-year schedule (from last final package or operator seed).",
            display_basis="Per-unit schedule.",
        ),
        basis_columns=basis_columns,
        total_columns=total_columns,
        rows=unit_rows,
    )


def resolve_prior_assessment_matrix(
    connection: Any,
    *,
    property_id: int,
    fiscal_year: int,
    hoa_name: str = "",
    frozen_prior: Optional[dict[str, Any]] = None,
) -> Optional[AssessmentScheduleMatrix]:
    """Resolve prior matrix for package year ``fiscal_year``.

    If ``frozen_prior`` is provided (finalize re-render), use it first.
    """
    if frozen_prior is not None:
        try:
            return AssessmentScheduleMatrix.model_validate(frozen_prior)
        except Exception:
            logger.exception("prior schedule: invalid frozen prior_assessment_matrix")

    prior_year = int(fiscal_year) - 1
    from_package = load_finalized_assessment_matrix(
        connection, property_id=property_id, fiscal_year=prior_year,
    )
    if from_package is not None:
        return from_package

    seed = load_prior_seed(connection, property_id=property_id)
    if seed is None:
        return None
    seed_year, rows = seed
    name = hoa_name or "Association"
    return matrix_from_seed_rows(hoa_name=name, fiscal_year=seed_year, rows=rows)


def extract_schedule_rows_from_pdf_text(text: str) -> list[dict[str, Any]]:
    """Best-effort unit/monthly rows from PDF text (operator must confirm).

    Looks for lines like: ``513  1.780  553.09`` or ``Unit 513 ... $553.09``.
    """
    rows: list[dict[str, Any]] = []
    # unit  percent  monthly
    pat_three = re.compile(
        r"^\s*(\d{2,4}[A-Za-z]?)\s+(\d{1,2}\.\d{2,4})\s+([\d,]+\.\d{2})\s*$"
    )
    # unit  monthly only
    pat_two = re.compile(
        r"^\s*(\d{2,4}[A-Za-z]?)\s+\$?([\d,]+\.\d{2})\s*$"
    )
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith("total") or line.lower().startswith("annual"):
            continue
        m = pat_three.match(line)
        if m:
            rows.append(
                {
                    "recipient_label": m.group(1),
                    "percent_of_total": m.group(2),
                    "monthly": m.group(3).replace(",", ""),
                }
            )
            continue
        m2 = pat_two.match(line)
        if m2:
            rows.append(
                {
                    "recipient_label": m2.group(1),
                    "monthly": m2.group(2).replace(",", ""),
                }
            )
    # de-dupe by label keeping last
    by_label: dict[str, dict[str, Any]] = {}
    for r in rows:
        by_label[r["recipient_label"]] = r
    return list(by_label.values())


def prior_status(
    connection: Any,
    *,
    property_id: int,
    fiscal_year: int,
) -> dict[str, Any]:
    """Status payload for the disclosure UI card."""
    prior_year = int(fiscal_year) - 1
    if load_finalized_assessment_matrix(
        connection, property_id=property_id, fiscal_year=prior_year,
    ) is not None:
        return {
            "status": "inherited",
            "prior_fiscal_year": prior_year,
            "source": "finalized_package",
            "message": f"Using finalized {prior_year} assessment schedule.",
        }
    seed = load_prior_seed(connection, property_id=property_id)
    if seed is not None:
        seed_year, rows = seed
        return {
            "status": "seeded",
            "prior_fiscal_year": seed_year,
            "source": "operator_seed",
            "row_count": len(rows),
            "message": f"Using operator-confirmed {seed_year} schedule ({len(rows)} rows).",
        }
    return {
        "status": "missing",
        "prior_fiscal_year": prior_year,
        "source": None,
        "message": (
            f"No prior-year assessment schedule for {prior_year}. "
            "Upload last year’s final package or finalize that year first."
        ),
    }


__all__ = [
    "SEED_JSON_COLUMN",
    "SEED_YEAR_COLUMN",
    "clear_prior_seed",
    "extract_schedule_rows_from_pdf_text",
    "load_finalized_assessment_matrix",
    "load_prior_seed",
    "matrix_from_seed_rows",
    "prior_status",
    "resolve_prior_assessment_matrix",
    "save_prior_seed",
]
