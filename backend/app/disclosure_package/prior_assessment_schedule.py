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
    try:
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
    except sqlite3.OperationalError:
        # Table missing in unit/e2e in-memory DBs — treat as no prior package.
        return None
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


# ── Gemini Vision extract (scanned board packages) ──────────────────────────

_MIN_TEXT_ROWS_TO_SKIP_VISION = 3
_CLASSIFY_BATCH_SIZE = 8
_CLASSIFY_DPI = 100
_EXTRACT_DPI = 200
_MAX_EXTRACT_PAGES = 6

_PAGE_CLASSIFY_PROMPT = """\
You classify pages from an HOA annual budget / disclosure package PDF.

For EACH image I send (labeled with its page number), say whether it is a
**per-unit assessment schedule** table — typically titled like:
  "YYYY Assessments Per Unit Per Month"
or "Assessments Per Unit" / unit roster with monthly assessment amounts.

YES if the page is primarily a table of units (or unit types) with monthly
assessment dollar amounts (often also ownership % / undivided interest).
NO for: cover letters, pro forma income statements, reserve component
schedules, insurance certificates, policies, election rules, TOC, notes.

Return JSON only matching the schema.
"""

_EXTRACT_PROMPT = """\
Extract the per-unit (or per unit-type) assessment schedule from these page
images of an HOA disclosure package.

Rules:
- Read every unit / recipient row on the assessment schedule pages.
- monthly = the monthly assessment amount for that unit (digits, optional
  decimals; strip $ and commas).
- percent_of_total = ownership % / undivided interest if shown; else null.
- recipient_label = unit number or group label as printed (e.g. "513", "101").
- fiscal_year = the year in the schedule title (e.g. 2025 from
  "2025 Assessments Per Unit Per Month"), or null if unclear.
- Prefer the schedule for year {preferred_year} if multiple year schedules
  appear; otherwise extract the clearest full unit roster.
- Do NOT invent units. Skip totals/annual footer rows.
- Return JSON only matching the schema.
"""


def _normalize_extracted_rows(raw_rows: list[Any]) -> list[dict[str, Any]]:
    """Normalize Gemini / text rows into API seed shape."""
    out: list[dict[str, Any]] = []
    for raw in raw_rows or []:
        if isinstance(raw, dict):
            data = raw
        else:
            data = raw.model_dump() if hasattr(raw, "model_dump") else {}
        label = str(
            data.get("recipient_label")
            or data.get("unit")
            or data.get("label")
            or ""
        ).strip()
        monthly = str(data.get("monthly") or data.get("monthly_assessment") or "").strip()
        monthly = monthly.replace("$", "").replace(",", "")
        if not label or not monthly:
            continue
        row: dict[str, Any] = {"recipient_label": label, "monthly": monthly}
        pct = data.get("percent_of_total") or data.get("percent") or data.get("ownership_percent")
        if pct is not None and str(pct).strip() != "":
            row["percent_of_total"] = str(pct).strip().replace("%", "")
        out.append(row)
    by_label: dict[str, dict[str, Any]] = {}
    for r in out:
        by_label[r["recipient_label"]] = r
    return list(by_label.values())


def _classify_assessment_schedule_pages(
    client: Any,
    *,
    model: str,
    rendered_pages: list[Any],
    preferred_year: Optional[int] = None,
) -> list[int]:
    """Return 1-based page numbers that look like unit assessment schedules.

    When ``preferred_year`` is set and some hits carry that year, only those
    pages are returned (e.g. 2025 table when packaging 2026).
    """
    from google.genai import types
    from pydantic import BaseModel, Field

    class _PageHit(BaseModel):
        page: int = Field(ge=1)
        is_assessment_schedule: bool
        schedule_year: Optional[int] = None

    class _PageBatch(BaseModel):
        pages: list[_PageHit]

    hits: list[tuple[int, Optional[int]]] = []
    for start in range(0, len(rendered_pages), _CLASSIFY_BATCH_SIZE):
        batch = rendered_pages[start : start + _CLASSIFY_BATCH_SIZE]
        parts: list[Any] = [types.Part.from_text(text=_PAGE_CLASSIFY_PROMPT)]
        for page in batch:
            parts.append(
                types.Part.from_bytes(data=page.content, mime_type="image/png")
            )
            parts.append(
                types.Part.from_text(text=f"(That was PDF page {page.page_number}.)")
            )
        try:
            response = client.models.generate_content(
                model=model,
                contents=[types.Content(role="user", parts=parts)],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                    response_schema=_PageBatch,
                ),
            )
        except Exception:
            logger.exception("prior schedule: page classification Gemini call failed")
            continue
        parsed = response.parsed
        if parsed is None:
            continue
        for entry in parsed.pages:
            if entry.is_assessment_schedule:
                hits.append((int(entry.page), entry.schedule_year))
    if not hits:
        return []
    if preferred_year is not None:
        preferred_pages = sorted(
            {p for p, y in hits if y is not None and int(y) == int(preferred_year)}
        )
        if preferred_pages:
            return preferred_pages
    return sorted({p for p, _y in hits})


def _extract_rows_from_schedule_pages(
    client: Any,
    *,
    model: str,
    rendered_pages_by_num: dict[int, Any],
    page_numbers: list[int],
    preferred_year: Optional[int],
) -> tuple[list[dict[str, Any]], Optional[int]]:
    """Vision extract unit rows from selected schedule pages."""
    from google.genai import types
    from pydantic import BaseModel, Field

    class _Row(BaseModel):
        recipient_label: str
        monthly: str
        percent_of_total: Optional[str] = None

    class _Extract(BaseModel):
        fiscal_year: Optional[int] = None
        rows: list[_Row] = Field(default_factory=list)

    preferred = preferred_year if preferred_year is not None else "the prior year"
    prompt = _EXTRACT_PROMPT.format(preferred_year=preferred)
    parts: list[Any] = [types.Part.from_text(text=prompt)]
    for page_num in page_numbers[:_MAX_EXTRACT_PAGES]:
        rendered = rendered_pages_by_num.get(page_num)
        if rendered is None:
            continue
        parts.append(
            types.Part.from_bytes(data=rendered.content, mime_type="image/png")
        )
        parts.append(
            types.Part.from_text(text=f"(Assessment schedule page {page_num}.)")
        )
    if len(parts) <= 1:
        return [], None
    try:
        response = client.models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=_Extract,
            ),
        )
    except Exception:
        logger.exception("prior schedule: vision extract Gemini call failed")
        return [], None
    parsed = response.parsed
    if parsed is None:
        return [], None
    rows = _normalize_extracted_rows(parsed.rows)
    year = parsed.fiscal_year
    return rows, year


def extract_prior_schedule_from_pdf_bytes(
    content: bytes,
    *,
    preferred_year: Optional[int] = None,
) -> dict[str, Any]:
    """Extract prior schedule rows from a disclosure PDF.

    1. Cheap text layer parse (works for digital PDFs).
    2. If too few rows: Gemini Vision classify pages + extract tables
       (scanned packages like Sharon Ridge).

    Returns dict: rows, method, fiscal_year, message, pages_used.
    """
    import os
    import tempfile
    from io import BytesIO

    text = ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        logger.info("prior schedule: pypdf text extract failed; will try vision")

    text_rows = extract_schedule_rows_from_pdf_text(text)
    if len(text_rows) >= _MIN_TEXT_ROWS_TO_SKIP_VISION:
        return {
            "rows": text_rows,
            "method": "pdf_text",
            "fiscal_year": preferred_year,
            "pages_used": [],
            "message": (
                f"Extracted {len(text_rows)} units from PDF text. "
                "Review and confirm before saving."
            ),
        }

    from app.dre_extraction.gemini_callbacks import (
        default_model_name,
        gemini_client_from_env,
    )
    from app.services.pdf_vlm_extractor import render_pdf_pages

    client = gemini_client_from_env()
    if client is None:
        return {
            "rows": text_rows,
            "method": "pdf_text" if text_rows else "none",
            "fiscal_year": preferred_year,
            "pages_used": [],
            "message": (
                "No unit/monthly rows in PDF text, and Gemini is not configured "
                "(set GEMINI_API_KEY / GEMINI_MODEL). Enter the schedule manually."
                if not text_rows
                else f"Only {len(text_rows)} text row(s); Gemini unavailable for vision. "
                "Review or enter remaining units manually."
            ),
        }

    model = default_model_name()
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        os.write(fd, content)
        os.close(fd)
        # Explicit page count — default DOCUMENT_VLM_MAX_PAGES is only 6 and
        # would miss mid-package assessment tables (e.g. Sharon Ridge ~p30/45).
        import fitz

        _doc = fitz.open(path)
        page_count = int(_doc.page_count)
        _doc.close()
        classify_pages = render_pdf_pages(
            path, max_pages=page_count, dpi=_CLASSIFY_DPI,
        )
        schedule_page_nums = _classify_assessment_schedule_pages(
            client,
            model=model,
            rendered_pages=classify_pages,
            preferred_year=preferred_year,
        )
        if not schedule_page_nums:
            return {
                "rows": text_rows,
                "method": "vision_no_pages",
                "fiscal_year": preferred_year,
                "pages_used": [],
                "message": (
                    "Gemini did not find an assessment-schedule table in this PDF. "
                    "Enter the schedule manually, or upload the pages that list "
                    "unit monthly assessments."
                ),
            }

        # High-DPI re-render of classified pages only (cost control)
        high_res_by_num: dict[int, Any] = {}
        try:
            import fitz

            doc = fitz.open(path)
            scale = _EXTRACT_DPI / 72.0
            matrix = fitz.Matrix(scale, scale)
            for page_num in schedule_page_nums[:_MAX_EXTRACT_PAGES]:
                if page_num < 1 or page_num > doc.page_count:
                    continue
                pix = doc[page_num - 1].get_pixmap(matrix=matrix)
                high_res_by_num[page_num] = type(
                    "RP",
                    (),
                    {
                        "page_number": page_num,
                        "content": pix.tobytes("png"),
                        "mime_type": "image/png",
                    },
                )()
            doc.close()
        except Exception:
            logger.exception(
                "prior schedule: high-DPI re-render failed; using classify DPI"
            )
            by_num = {p.page_number: p for p in classify_pages}
            high_res_by_num = {
                n: by_num[n] for n in schedule_page_nums if n in by_num
            }

        vision_rows, fiscal_year = _extract_rows_from_schedule_pages(
            client,
            model=model,
            rendered_pages_by_num=high_res_by_num,
            page_numbers=schedule_page_nums,
            preferred_year=preferred_year,
        )
        if not vision_rows and text_rows:
            return {
                "rows": text_rows,
                "method": "pdf_text_fallback",
                "fiscal_year": preferred_year,
                "pages_used": schedule_page_nums,
                "message": (
                    "Vision found schedule pages but no rows; kept text extract. "
                    "Review carefully."
                ),
            }
        if not vision_rows:
            return {
                "rows": [],
                "method": "vision_empty",
                "fiscal_year": preferred_year,
                "pages_used": schedule_page_nums,
                "message": (
                    f"Found possible schedule page(s) {schedule_page_nums} but "
                    "could not read unit rows. Enter the schedule manually."
                ),
            }
        year_out = fiscal_year if fiscal_year is not None else preferred_year
        return {
            "rows": vision_rows,
            "method": "gemini_vision",
            "fiscal_year": year_out,
            "pages_used": schedule_page_nums[:_MAX_EXTRACT_PAGES],
            "message": (
                f"Extracted {len(vision_rows)} units via Gemini Vision "
                f"(pages {schedule_page_nums[:_MAX_EXTRACT_PAGES]}). "
                "Review and confirm before saving."
            ),
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


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
    "extract_prior_schedule_from_pdf_bytes",
    "extract_schedule_rows_from_pdf_text",
    "load_finalized_assessment_matrix",
    "load_prior_seed",
    "matrix_from_seed_rows",
    "prior_status",
    "resolve_prior_assessment_matrix",
    "save_prior_seed",
]
