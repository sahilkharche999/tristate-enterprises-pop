"""
income_statement_parser.py
==========================

Section taxonomy and cell helpers shared by the budget pipeline.

Budget-source files are read by Gemini (see budget_history_service and
pdf_vlm_extractor); this module no longer guesses columns. It provides:
- the canonical section kinds and the section-header state machine used by
  the normalized statement workbook,
- account-code helpers,
- financial cell parsing ($, commas, parentheses negatives, dashes),
- the .xls reader used to show uploaded Excel 97-2003 files.
"""
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal, NamedTuple, Optional, get_args

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical section taxonomy — single source of truth for section classification.
# ---------------------------------------------------------------------------
# Imported by: budget_history_service, normalized_statement_workbook,
# financial_document_extraction (Pydantic model), and taxonomy invariant tests.
#
# Every layer of the pipeline (Gemini extraction, parser state machine,
# `_infer_category`, frontend LineItem) MUST use these exact strings.
SectionKind = Literal["income", "operating", "reserve_income", "reserve_expense"]
SECTION_KINDS: tuple[str, ...] = get_args(SectionKind)

# Sections whose items are displayed as read-only (not editable, not in totals).
# Changing this single constant changes read-only behavior everywhere.
READ_ONLY_SECTIONS: frozenset[str] = frozenset({"reserve_income", "reserve_expense"})

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Section state machine: maps lowercase section header prefixes to category states.
# Reserve is split into reserve_income and reserve_expense so the frontend
# can show them as separate groups with correct totals.
_SECTION_TRANSITIONS: dict[str, str] = {
    "operating income": "income",
    "operating expense": "operating",
    "reserve income": "reserve_income",
    "reserve expense": "reserve_expense",
    "reserve expenses": "reserve_expense",
    "reserve contribution": "reserve_income",
}

_ACCOUNT_CODE_PATTERN = re.compile(r"^(\d{4,6})\s*-")

# Reserve study sub-section keywords (for read_only flagging)


def _normalize(text: str) -> str:
    """NFC unicode, collapse whitespace, strip, lowercase."""
    text = unicodedata.normalize("NFC", str(text))
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


class ParsedCell(NamedTuple):
    """Outcome of parsing one financial cell (C5).

    ``kind`` distinguishes states the legacy ``-> float`` parsers collapsed
    into ``0.0``:

    - ``ok``: a real numeric value is in ``value``.
    - ``empty``: blank / None \u2014 "no value present".
    - ``dash``: only a dash / em-dash \u2014 "no value stated" (explicit
      not-applicable, distinct from a true ``0``).
    - ``unparseable``: text we could not turn into a number (OCR noise,
      European decimals, malformed input). ``value`` is ``None``; this MUST
      surface for operator review rather than silently become ``0``.

    ``value`` is ``None`` for every kind except ``ok``. Zero is only ever
    produced by a cell that actually states zero.
    """

    value: Optional[float]
    kind: str
    raw: str


def parse_financial_cell(value: Any) -> ParsedCell:
    """Shared numeric normalizer for every financial-cell parse path (C5).

    Normalization order is fixed so sign survives currency/thousands
    formatting: strip ``$``, commas, and spaces FIRST, then detect the
    negative forms. Fixes the class of defects where ``($1,234)`` and
    ``1,234-`` silently became ``0`` (sign AND magnitude lost).

    Never returns ``0`` as a fallback \u2014 unreadable input yields
    ``kind='unparseable'`` with ``value=None``.
    """
    if value is None:
        return ParsedCell(None, "empty", "")
    # bool is an int subclass \u2014 never a financial amount.
    if isinstance(value, bool):
        return ParsedCell(None, "unparseable", str(value))
    if isinstance(value, (int, float)):
        return ParsedCell(float(value), "ok", str(value))

    text = str(value).strip()
    if not text:
        return ParsedCell(None, "empty", str(value))
    if text in {"-", "\u2014", "\u2013"}:
        return ParsedCell(None, "dash", text)

    # Strip currency, thousands separators, and spaces BEFORE detecting
    # negative forms, so a currency symbol inside parens ("($1,234)") no
    # longer defeats the negative match.
    cleaned = (
        text.replace("$", "")
        .replace(",", "")
        .replace(" ", "")
        .replace("\u00a0", "")
        .strip()
    )

    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1].strip()
    elif cleaned.endswith("-"):  # trailing-minus accounting negative
        negative = True
        cleaned = cleaned[:-1].strip()
    elif cleaned.startswith("-"):
        negative = True
        cleaned = cleaned[1:].strip()
    elif cleaned.startswith("+"):
        cleaned = cleaned[1:].strip()

    if not cleaned:
        return ParsedCell(None, "unparseable", text)
    try:
        num = float(cleaned)
    except ValueError:
        return ParsedCell(None, "unparseable", text)
    return ParsedCell(-num if negative else num, "ok", text)


# ---------------------------------------------------------------------------
# Section state machine
# ---------------------------------------------------------------------------

def _match_section_header(label: str) -> Optional[str]:
    """Return the section category for a recognized section header, or None.

    Uses case-insensitive starts-with matching — consistent with the HOA
    accounting software that all 81 HOAs use.

    Args:
        label: Cell text from column A of the row.

    Returns:
        "income", "operating", or "reserve" if recognized; None otherwise.
    """
    if not label or not label.strip():
        return None
    normalized = _normalize(label)
    for prefix, state in _SECTION_TRANSITIONS.items():
        if normalized.startswith(prefix):
            return state
    return None


def _classify_by_account_code(account_code: Optional[int]) -> str:
    """Fallback classification when no section headers detected.

    Account code ranges:
      40000-49999 = income (or reserve_income if in reserve context)
      50000-89999 = operating
      90000+      = reserve_expense
    """
    if account_code is None:
        return "operating"
    if 40000 <= account_code <= 49999:
        return "income"
    if 50000 <= account_code <= 89999:
        return "operating"
    if account_code >= 90000:
        return "reserve_expense"
    return "operating"


def _extract_account_code(label: str) -> Optional[int]:
    """Extract leading 4-6 digit account code from label, or None."""
    if not label:
        return None
    m = _ACCOUNT_CODE_PATTERN.match(label.strip())
    if m:
        return int(m.group(1))
    # Try raw leading digits
    head = label.split("-", 1)[0].strip()
    if head.isdigit():
        return int(head)
    return None


# ---------------------------------------------------------------------------
# Column auto-detection
# ---------------------------------------------------------------------------


def _read_xls_rows(path: str, sheet_name: str = "Income Statement") -> list:
    """Read .xls (Excel 97-2003) file into normalized list of lists.

    Uses xlrd. Normalizes empty string "" to None to match openpyxl convention.
    """
    import xlrd

    # H13: formatting_info=True is what exposes xlrd's rowinfo_map/colinfo_map
    # hidden flags. When it's unavailable (some .xls variants), we cannot see
    # hidden state — proceed as before and note the limitation.
    have_formatting = True
    try:
        wb = xlrd.open_workbook(path, formatting_info=True)
    except Exception:
        wb = xlrd.open_workbook(path)
        have_formatting = False
    try:
        ws = wb.sheet_by_name(sheet_name)
    except xlrd.XLRDError:
        ws = wb.sheet_by_index(0)

    # Hidden rows/columns from the format info (empty sets when unavailable).
    hidden_rows = set()
    hidden_cols = set()
    if have_formatting:
        for r, info in (getattr(ws, "rowinfo_map", {}) or {}).items():
            if getattr(info, "hidden", 0):
                hidden_rows.add(r)
        for c, info in (getattr(ws, "colinfo_map", {}) or {}).items():
            if getattr(info, "hidden", 0):
                hidden_cols.add(c)
    elif Path(path).suffix.lower() == ".xls":
        logger.warning(
            "H13: could not read hidden-cell formatting for %s; hidden rows/"
            "columns (if any) were NOT excluded.", Path(path).name,
        )

    rows = []
    for r in range(ws.nrows):
        row = []
        for c in range(ws.ncols):
            # Mask hidden rows/columns to None (same as the xlsx path).
            if r in hidden_rows or c in hidden_cols:
                row.append(None)
                continue
            v = ws.cell_value(r, c)
            # Normalize blank cells to None (openpyxl convention)
            row.append(None if v == "" else v)
        rows.append(row)

    if hidden_rows or hidden_cols:
        logger.warning(
            "H13: excluded %d hidden row(s) and %d hidden column(s) from %s",
            len(hidden_rows), len(hidden_cols), Path(path).name,
        )

    # Propagate merged cell values to the leftmost column of each merge.
    # xlrd stores merged_cells as (row_lo, row_hi, col_lo, col_hi) ranges.
    # The cell value may live at any position in the merge, so copy it to col_lo
    # if col_lo is currently None.
    for rlo, rhi, clo, chi in getattr(ws, "merged_cells", []):
        # Find the value anywhere in this merge range
        val = None
        for r in range(rlo, rhi):
            for c in range(clo, chi):
                if r < len(rows) and c < len(rows[r]) and rows[r][c] is not None:
                    val = rows[r][c]
                    break
            if val is not None:
                break
        # Place the value at (rlo, clo) — the top-left of the merge
        if val is not None and rlo < len(rows) and clo < len(rows[rlo]):
            rows[rlo][clo] = val

    return rows
