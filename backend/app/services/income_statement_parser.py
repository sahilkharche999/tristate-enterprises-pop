"""
income_statement_parser.py
==========================

Centralized income statement parser module.

Provides:
- Section state machine: classifies line items by their section position,
  not by label keywords (fixes "reserve" label misclassification).
- Column auto-detection: 3-tier strategy:
    1. Alias matching (multi-row header aware)
    2. LLM zero-shot mapping (if fewer than 2 aliases matched)
    3. Hardcoded fallback indices (if LLM also fails)
- Multi-format file reading: .xlsx (openpyxl), .xls (xlrd), .pdf (pdfplumber)
- Financial float parsing: handles $, commas, parentheses negatives, dashes
- Row-level validation: deferred (YTD vs Annual comparison is cross-period, needs YTD Budget extraction)

Public API:
    parse_income_statement(path, sheet_name) -> list[dict]
    detect_columns(rows) -> dict[str, int]
    parse_rows_with_sections(rows, col_indices) -> list[dict]
    _match_section_header(label) -> Optional[str]
    _parse_financial_float(value) -> float

Exports used by downstream consumers (budget_history_service, generate_budget_pipeline):
    parse_income_statement
    detect_columns
    _match_section_header
    _parse_financial_float
"""
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal, Optional, get_args

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

# Column header aliases for auto-detection (case-insensitive, stripped)
_HEADER_ALIASES: dict[str, list[str]] = {
    "ytd_actual": ["year to date actual", "ytd actual", "ytd actuals", "year-to-date actual", "ytd"],
    "annual_budget": ["annual budget", "annual budgeted", "total budget"],
    "variance": ["variance", "difference", "$ variance"],
}

# Group-level header terms that span multiple columns
# These appear in a parent row above the detail row (e.g., "Year To Date" above "Actual | Budget | Variance")
_GROUP_HEADERS: dict[str, str] = {
    "year to date": "ytd",
    "annual budget": "annual_budget",
    "current period": "current_period",
}

# Hardcoded fallback column indices (0-based) — used when all detection tiers fail
_FALLBACK_COLUMNS: dict[str, int] = {
    "ytd_actual": 19,
    "annual_budget": 32,
    "variance": 26,
}

# Parentheses negative number pattern: (1,234.56) -> -1234.56
_PAREN_PATTERN = re.compile(r"^\(([0-9,\.]+)\)$")

# Account code extraction: first segment before "-" if it is digits
_ACCOUNT_CODE_PATTERN = re.compile(r"^(\d{4,6})\s*-")

# PDF: section headers are at x0 < this threshold (left-margin indented items are line items)
_PDF_SECTION_X0_THRESHOLD = 20.0

# Reserve study sub-section keywords (for read_only flagging)
_RESERVE_STUDY_SUBHEADERS = {
    "reserve expenses (per reserve study)",
    "reserve expenses per reserve study",
}

_LLM_REQUIRED_COLUMNS = ("ytd_actual", "annual_budget", "variance")
_LLM_PROMPT_MAX_COLUMNS = 40
_LLM_LEFT_SHIFT_LIMIT = 5
_LLM_NUMERIC_CONFIDENCE_RATIO = 0.6


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """NFC unicode, collapse whitespace, strip, lowercase."""
    text = unicodedata.normalize("NFC", str(text))
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _cell_text(row: list, col: int) -> str:
    """Return stripped string for cell at col, or '' if out of range or None."""
    if 0 <= col < len(row) and row[col] is not None:
        return str(row[col]).strip()
    return ""


def _safe_get(row: list, col: int) -> Any:
    """Return cell value at col or None if out of range."""
    return row[col] if 0 <= col < len(row) else None


def _is_numeric_cell(value: Any) -> bool:
    """Check if a cell value is numeric (int, float, or parseable string)."""
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    text = str(value).strip().replace(",", "").replace("$", "")
    if text in ("", "-", "--", "None"):
        return False
    try:
        float(text.strip("()"))
        return True
    except (ValueError, TypeError):
        return False


def _max_visible_prompt_width(header_rows: list, sample_rows: list) -> int:
    """Return the max visible width LLM can reference from the compressed prompt."""
    widths = [
        min(len(row), _LLM_PROMPT_MAX_COLUMNS)
        for row in [*header_rows, *sample_rows]
        if row is not None
    ]
    return max(widths, default=0)


def _build_line_item_sample_rows(rows: list, limit: int = 5) -> list:
    """Collect the first few true line-item rows for deterministic numeric validation."""
    sample_rows = []
    for row in rows:
        col_a = _cell_text(row, 0)
        col_b = _cell_text(row, 1)
        if col_a and not col_b and _match_section_header(col_a) is not None:
            continue

        label = col_b or col_a
        if not label or _normalize(label).startswith("total "):
            continue
        if _extract_account_code(label) is None:
            continue

        sample_rows.append(row)
        if len(sample_rows) >= limit:
            break

    return sample_rows


def _reject_llm_mapping(reason: str, mapping: Optional[dict], detail: str = "") -> None:
    """Log a rejected LLM mapping with a stable reason code."""
    suffix = f" ({detail})" if detail else ""
    logger.warning("Rejecting LLM column mapping [%s]: %s%s", reason, mapping, suffix)


def _sanitize_llm_column_map(col_map: Optional[dict], max_prompt_width: int) -> Optional[dict]:
    """Reject malformed or impossible LLM column suggestions before validation."""
    if not col_map or max_prompt_width <= 0:
        _reject_llm_mapping("out_of_range", col_map, "no visible prompt columns")
        return None

    sanitized: dict[str, int] = {}
    for key in _LLM_REQUIRED_COLUMNS:
        if key not in col_map or type(col_map[key]) is not int:
            _reject_llm_mapping("invalid_type", col_map, f"{key} must be int")
            return None

        idx = col_map[key]
        if idx < 0:
            _reject_llm_mapping("negative", col_map, f"{key}={idx}")
            return None
        if idx >= max_prompt_width:
            _reject_llm_mapping("out_of_range", col_map, f"{key}={idx}, width={max_prompt_width}")
            return None
        sanitized[key] = idx

    if len(set(sanitized.values())) != len(sanitized):
        _reject_llm_mapping("duplicate", sanitized)
        return None

    return sanitized


def _numeric_ratio_for_column(data_rows: list, col_idx: int) -> float:
    """Return the fraction of sample rows with numeric values in a candidate column."""
    if not data_rows:
        return 0.0
    numeric_count = sum(1 for row in data_rows if _is_numeric_cell(_safe_get(row, col_idx)))
    return numeric_count / len(data_rows)


def _repair_llm_column_left(col_idx: int, data_rows: list) -> Optional[int]:
    """Accept the proposed column or shift left to the first high-confidence numeric column."""
    for offset in range(_LLM_LEFT_SHIFT_LIMIT + 1):
        candidate = col_idx - offset
        if candidate < 0:
            break
        if _numeric_ratio_for_column(data_rows, candidate) >= _LLM_NUMERIC_CONFIDENCE_RATIO:
            return candidate
    return None


def _validate_llm_columns_against_data(col_map: Optional[dict], data_rows: list) -> Optional[dict]:
    """Require LLM-proposed columns to prove themselves on actual line-item rows."""
    if not col_map:
        return None
    if not data_rows:
        _reject_llm_mapping("low_confidence", col_map, "no line-item sample rows")
        return None

    validated: dict[str, int] = {}
    for key, col_idx in col_map.items():
        repaired = _repair_llm_column_left(col_idx, data_rows)
        if repaired is None:
            ratio = _numeric_ratio_for_column(data_rows, col_idx)
            _reject_llm_mapping("low_confidence", col_map, f"{key}={col_idx}, ratio={ratio:.2f}")
            return None
        if repaired != col_idx:
            logger.info("LLM column %s adjusted LEFT from %d to %d", key, col_idx, repaired)
        validated[key] = repaired

    if len(set(validated.values())) != len(validated):
        _reject_llm_mapping("duplicate", validated, "after left-shift repair")
        return None

    return validated


# ---------------------------------------------------------------------------
# Financial float parser
# ---------------------------------------------------------------------------

def _parse_financial_float(value: Any) -> float:
    """Parse a financial value to float.

    Handles:
    - Parentheses negatives: "(133.86)" -> -133.86
    - Dollar signs: "$1,500.00" -> 1500.0
    - Commas: "1,043,706.24" -> 1043706.24
    - Dashes / em-dashes: "-", "—" -> 0.0
    - None, empty string -> 0.0
    - Numeric types: pass through as float

    Does NOT misinterpret "-" as a negative number (it means zero in financial docs).
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text in {"-", "\u2014", "\u2013", ""}:
        return 0.0
    # Strip currency symbols
    # Parentheses negative: "(1,500)" -> -1500.0
    m = _PAREN_PATTERN.match(text)
    if m:
        return -float(m.group(1).replace(",", ""))
    text = text.replace("$", "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


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

def _llm_column_fallback(header_rows: list, sample_rows: list) -> Optional[dict]:
    """Tier 2: LLM fallback for zero-shot column detection.

    Sends header rows + up to 3 sample data rows to LLM for zero-shot
    schema mapping. Uses sheet compression to minimize tokens.

    Args:
        header_rows: The first <=10 rows of the sheet (header area).
        sample_rows: A few data rows (up to 3) to help the LLM understand the layout.

    Returns:
        Dict with keys {ytd_actual, annual_budget, variance} (0-based indices),
        or None if LLM fails.
    """
    import asyncio
    from pydantic import BaseModel

    class ColumnMapping(BaseModel):
        ytd_actual: int
        annual_budget: int
        variance: int

    # Compress: take header rows + up to 3 sample data rows
    compressed = []
    for row in header_rows:
        compressed.append([str(cell) if cell is not None else "" for cell in row[:_LLM_PROMPT_MAX_COLUMNS]])
    for row in sample_rows[:3]:
        compressed.append([str(cell) if cell is not None else "" for cell in row[:_LLM_PROMPT_MAX_COLUMNS]])

    # Format as a readable table for the LLM
    table_text = ""
    for i, row in enumerate(compressed):
        non_empty = [(j, v) for j, v in enumerate(row) if v.strip()]
        if non_empty:
            table_text += f"Row {i}: " + ", ".join(f"col{j}={v}" for j, v in non_empty) + "\n"

    if not table_text.strip():
        return None

    messages = [
        {
            "role": "system",
            "content": (
                "You are a financial spreadsheet analyst. Given rows from an income statement, "
                "identify the 0-based column indices for: ytd_actual (Year-To-Date Actual), "
                "annual_budget (Annual Budget), and variance. Return only non-negative 0-based "
                "indices that reference visible colN entries in the provided rows. Never return "
                "negative placeholders like -1, never reuse the same index for multiple fields, "
                "and never guess hidden columns. "
                'Return JSON: {"ytd_actual": <int>, "annual_budget": <int>, "variance": <int>}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Here are the header rows and first 3 data rows from an income statement:\n\n"
                f"{table_text}\n\nIdentify the 0-based column indices. Use only visible colN "
                f"entries from the rows above, and do not use negative values or duplicates."
            ),
        },
    ]

    try:
        from ..ai_implementation.pipeline.llm_client import call_llm

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures

            def _run_in_new_loop():
                new_loop = asyncio.new_event_loop()
                try:
                    return new_loop.run_until_complete(
                        call_llm(messages, ColumnMapping, temperature=0.0, timeout=15.0)
                    )
                finally:
                    new_loop.close()

            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(_run_in_new_loop).result()
        else:
            result = asyncio.run(call_llm(messages, ColumnMapping, temperature=0.0, timeout=15.0))

        if result is not None:
            mapping = {
                "ytd_actual": result.ytd_actual,
                "annual_budget": result.annual_budget,
                "variance": result.variance,
            }
            logger.info("LLM column detection succeeded: %s", mapping)
            return mapping

    except Exception as e:
        logger.warning("LLM column fallback failed: %s", e)

    return None


def _validate_columns_against_data(col_map: dict, data_rows: list) -> dict:
    """Validate that matched columns have numeric data. If not, scan LEFT.

    Merged cells can cause header text to sit at the RIGHT edge of the merge
    while data lives at the LEFT edge. This function checks each column and
    adjusts leftward if needed.
    """
    if not data_rows or not col_map:
        return col_map
    validated = {}
    for key, col_idx in col_map.items():
        has_data = any(
            _is_numeric_cell(_safe_get(r, col_idx))
            for r in data_rows
        )
        if has_data:
            validated[key] = col_idx
        else:
            found = False
            for offset in range(1, 6):
                candidate = col_idx - offset
                if candidate < 0:
                    break
                count = sum(1 for r in data_rows if _is_numeric_cell(_safe_get(r, candidate)))
                if count >= max(len(data_rows) // 2, 1):
                    logger.info("Column %s: position %d has no data, adjusted LEFT to %d", key, col_idx, candidate)
                    validated[key] = candidate
                    found = True
                    break
            if not found:
                validated[key] = col_idx
    return validated


def detect_columns(rows: list) -> dict:
    """Scan rows 0-9 for column header row(s). Return 0-based column indices.

    3-tier detection strategy:
    1. Alias matching against known header terms (multi-row header aware).
       - For multi-row headers like Esprit Park (group row + detail row),
         find group spans first, then locate "Actual"/"Variance" within groups.
    2. If fewer than 2 required columns found -> LLM zero-shot mapping
       with headers + 3 sample rows (per CONTEXT.md locked decision).
    3. If LLM also fails -> hardcoded fallback indices.

    Returns _FALLBACK_COLUMNS if all tiers fail.
    """
    scan_rows = rows[:10] if len(rows) >= 10 else rows

    matched: dict[str, int] = {}

    # -- Tier 1a: Group-header pass (multi-row headers like Esprit Park) --
    # Find rows that contain group labels ("Year To Date", "Annual Budget", "Current Period")
    # Record which columns they span.
    group_spans: dict[str, tuple[int, int]] = {}  # group_key -> (start_col, end_col)

    for row_idx, row in enumerate(scan_rows):
        group_positions: list[tuple[int, str]] = []
        for col_idx, cell in enumerate(row):
            if cell is None:
                continue
            cell_norm = _normalize(str(cell))
            for grp_prefix, grp_key in _GROUP_HEADERS.items():
                if cell_norm.startswith(grp_prefix):
                    group_positions.append((col_idx, grp_key))

        if not group_positions:
            continue

        # Assign spans: from this col to the next group's start col (or end of row)
        group_positions.sort(key=lambda x: x[0])
        for i, (col_start, grp_key) in enumerate(group_positions):
            col_end = group_positions[i + 1][0] - 1 if i + 1 < len(group_positions) else len(row) - 1
            group_spans[grp_key] = (col_start, col_end)

    # -- Tier 1b: Detail-row alias pass --
    # If group spans were found, look for "Actual"/"Variance"/"Budget" in the detail row
    # and constrain to appropriate spans.
    for row_idx, row in enumerate(scan_rows):
        for col_idx, cell in enumerate(row):
            if cell is None:
                continue
            cell_norm = _normalize(str(cell))

            for canonical_key, aliases in _HEADER_ALIASES.items():
                if canonical_key in matched:
                    continue
                for alias in aliases:
                    if cell_norm == alias or cell_norm.startswith(alias):
                        matched[canonical_key] = col_idx
                        break

            # Special handling for bare "Actual" and "Budget" with group context
            if "ytd_actual" not in matched and cell_norm == "actual":
                ytd_span = group_spans.get("ytd")
                if ytd_span:
                    start, end = ytd_span
                    if start <= col_idx <= end:
                        matched["ytd_actual"] = col_idx
                elif not group_spans:
                    # No group context — accept the first "Actual" we find
                    matched.setdefault("ytd_actual", col_idx)

            if "annual_budget" not in matched and cell_norm in ("actual", "budget"):
                ab_span = group_spans.get("annual_budget")
                if ab_span:
                    start, end = ab_span
                    if start <= col_idx <= end:
                        matched["annual_budget"] = col_idx

            if "variance" not in matched and cell_norm in ("variance",):
                ytd_span = group_spans.get("ytd")
                if ytd_span:
                    start, end = ytd_span
                    if start <= col_idx <= end:
                        matched["variance"] = col_idx
                elif not group_spans:
                    matched.setdefault("variance", col_idx)

    # -- Tier 1c: Data-validation pass --
    data_rows = [r for r in rows[5:15] if len(r) > 2 and r[1]]  # rows with col B labels
    if data_rows and matched:
        matched = _validate_columns_against_data(matched, data_rows)

    # Also fix variance: prefer YTD variance over Current Period variance
    if "variance" in matched and group_spans.get("ytd"):
        ytd_start, ytd_end = group_spans["ytd"]
        if not (ytd_start - 3 <= matched["variance"] <= ytd_end):
            for row_idx, row in enumerate(scan_rows):
                for col_idx, cell in enumerate(row):
                    if cell is None:
                        continue
                    cell_norm = _normalize(str(cell))
                    if cell_norm == "variance" and ytd_start - 3 <= col_idx <= ytd_end:
                        matched["variance"] = col_idx
                        break

    if len(matched) >= 2:
        for key in _FALLBACK_COLUMNS:
            matched.setdefault(key, _FALLBACK_COLUMNS[key])
        matched["_detection_tier"] = 1
        logger.debug("Column detection tier 1 (alias): %s", matched)
        return matched

    # -- Tier 2: LLM fallback --
    header_rows = scan_rows
    sample_rows = rows[10:15] if len(rows) > 10 else rows[5:]
    line_item_sample_rows = _build_line_item_sample_rows(rows)
    max_prompt_width = _max_visible_prompt_width(header_rows, sample_rows)
    llm_result = _llm_column_fallback(header_rows, sample_rows)
    llm_result = _sanitize_llm_column_map(llm_result, max_prompt_width)
    llm_result = _validate_llm_columns_against_data(llm_result, line_item_sample_rows)
    if llm_result is not None and len(llm_result) >= 2:
        for key in _FALLBACK_COLUMNS:
            llm_result.setdefault(key, _FALLBACK_COLUMNS[key])
        llm_result["_detection_tier"] = 2
        logger.info("Column detection tier 2 (LLM): %s", llm_result)
        return llm_result

    # -- Tier 3: Hardcoded fallback --
    logger.warning("Column detection: all tiers failed, using hardcoded fallback %s", _FALLBACK_COLUMNS)
    result = dict(_FALLBACK_COLUMNS)
    result["_detection_tier"] = 3
    return result


# ---------------------------------------------------------------------------
# Row parsing with section state machine
# ---------------------------------------------------------------------------

def parse_rows_with_sections(
    rows: list,
    col_indices: dict,
) -> list:
    """Walk rows top-to-bottom, track section state, classify each line item.

    Classification is determined entirely by section position, NOT by label text.
    This is the core fix: "90000 - Reserve - Allocation/Transfer" under
    Operating Expense stays category="operating" and read_only=False.

    Args:
        rows: Normalized rows (list of lists), 0-based columns.
        col_indices: 0-based column indices from detect_columns().

    Returns:
        List of line item dicts with keys:
        line_item_key, account_code, category, label, ytd_actual,
        annual_budget, projection, percent_change, read_only,
        section, validation_warning, raw
    """
    # Pop metadata key so it doesn't interfere with column lookups
    col_indices = {k: v for k, v in col_indices.items() if not k.startswith("_")}

    current_section = "operating"  # safe default per spec
    in_reserve_study_block = False
    section_transition_count = 0
    line_items = []

    for row in rows:
        col_a = _cell_text(row, 0)
        col_b = _cell_text(row, 1)

        # Section / sub-section header: col A has text, col B is empty
        if col_a and not col_b:
            label_norm = _normalize(col_a)

            # Skip "Total X" section rows
            if label_norm.startswith("total "):
                continue

            # Check for a major section transition
            new_section = _match_section_header(col_a)
            if new_section is not None:
                current_section = new_section
                in_reserve_study_block = False  # reset on major section change
                section_transition_count += 1

            # Track reserve study sub-section (triggers read_only=True)
            if label_norm.startswith("reserve expenses"):
                in_reserve_study_block = True

            continue  # section/sub-section row — not a data row

        # Line item: col B has text (or col A with no col B alternative)
        label = col_b if col_b else col_a
        if not label:
            continue

        # Skip "Total X" data rows
        if _normalize(label).startswith("total "):
            continue

        account_code = _extract_account_code(label)
        is_read_only = current_section in READ_ONLY_SECTIONS

        ytd_idx = col_indices.get("ytd_actual", _FALLBACK_COLUMNS["ytd_actual"])
        annual_idx = col_indices.get("annual_budget", _FALLBACK_COLUMNS["annual_budget"])
        variance_idx = col_indices.get("variance", _FALLBACK_COLUMNS["variance"])
        projection_idx = col_indices.get("projection", 37)
        pct_change_idx = col_indices.get("percent_change", 38)

        ytd_actual = _parse_financial_float(_safe_get(row, ytd_idx))
        annual_budget = _parse_financial_float(_safe_get(row, annual_idx))
        variance = _parse_financial_float(_safe_get(row, variance_idx))

        line_items.append(
            {
                "line_item_key": str(account_code if account_code is not None else label),
                "account_code": account_code,
                "category": current_section,
                "label": label.strip(),
                "ytd_actual": ytd_actual,
                "annual_budget": annual_budget,
                "projection": _parse_financial_float(_safe_get(row, projection_idx)),
                "percent_change": _parse_financial_float(_safe_get(row, pct_change_idx)),
                "read_only": is_read_only,
                "section": current_section,
                "raw": {
                    "section": current_section,
                    "label": col_b.strip() if col_b else None,
                },
            }
        )

    # Fallback: if no section headers were detected, reclassify by account code ranges
    if section_transition_count == 0 and line_items:
        for item in line_items:
            item["category"] = _classify_by_account_code(item["account_code"])
            item["section"] = item["category"]

    return line_items


# ---------------------------------------------------------------------------
# File format readers
# ---------------------------------------------------------------------------

def _read_xlsx_rows(path: str, sheet_name: str = "Income Statement") -> list:
    """Read .xlsx file into normalized list of lists.

    Uses openpyxl data_only=True to read cached values (not formulas).
    Returns rows as list of lists with None for blank cells.
    """
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    try:
        ws = wb[sheet_name] if sheet_name in wb.sheetnames else wb[wb.sheetnames[0]]
        rows = []
        for r in range(1, ws.max_row + 1):
            row = [ws.cell(row=r, column=c).value for c in range(1, ws.max_column + 1)]
            rows.append(row)
        return rows
    finally:
        wb.close()


def _read_xls_rows(path: str, sheet_name: str = "Income Statement") -> list:
    """Read .xls (Excel 97-2003) file into normalized list of lists.

    Uses xlrd. Normalizes empty string "" to None to match openpyxl convention.
    """
    import xlrd

    try:
        wb = xlrd.open_workbook(path, formatting_info=True)
    except Exception:
        wb = xlrd.open_workbook(path)
    try:
        ws = wb.sheet_by_name(sheet_name)
    except xlrd.XLRDError:
        ws = wb.sheet_by_index(0)

    rows = []
    for r in range(ws.nrows):
        row = []
        for c in range(ws.ncols):
            v = ws.cell_value(r, c)
            # Normalize blank cells to None (openpyxl convention)
            row.append(None if v == "" else v)
        rows.append(row)

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


def _pdf_lines_to_rows(lines: list) -> list:
    """Convert grouped PDF word lines into the same row format as Excel readers.

    Uses x0 coordinates from pdfplumber to map each word to the correct column.
    Detects column positions from the header row (the one with "Actual", "Budget",
    "Variance" labels), then assigns numeric values to the nearest column.

    Args:
        lines: List of (first_x0, word_list) tuples, one per visual line.

    Returns:
        List of rows matching Excel format (col 0=A, col 1=B, cols 2+=data).
        Includes synthesized header rows so detect_columns() can find column names.
    """
    # -- Step 1: Find the detail header row (has >=3 of "actual"/"budget"/"variance") --
    # Also gather header words from adjacent lines (±1) to handle split headers
    # where e.g. "Annual/Budget" sits on a slightly different y-coordinate.
    header_keywords = {"actual", "budget", "variance"}
    header_line_idx = None
    header_x0s: list[tuple[float, str]] = []

    for idx, (first_x0, word_list) in enumerate(lines):
        kw_count = sum(1 for w in word_list if w["text"].lower() in header_keywords)
        if kw_count >= 3:
            header_line_idx = idx
            # Collect keywords from this line AND adjacent lines (±1)
            for adj_idx in range(max(0, idx - 1), min(len(lines), idx + 2)):
                _, adj_words = lines[adj_idx]
                for w in adj_words:
                    if w["text"].lower() in header_keywords:
                        # Only add if x0 is not already covered (within 20px)
                        if not any(abs(w["x0"] - ex0) < 20 for ex0, _ in header_x0s):
                            header_x0s.append((w["x0"], w["text"]))
            header_x0s.sort(key=lambda t: t[0])
            break

    if not header_x0s:
        # No header row found — fall back to raw text in col 1
        rows = []
        for first_x0, word_list in lines:
            row = [None] * 10
            text = " ".join(w["text"] for w in word_list)
            if first_x0 < _PDF_SECTION_X0_THRESHOLD:
                row[0] = text
            else:
                row[1] = text
            rows.append(row)
        return rows

    # Column centers: x0 positions of each header word
    col_centers = [x0 for x0, _ in header_x0s]
    # The label area ends where the first numeric column starts
    label_x_max = col_centers[0] - 20

    # -- Step 2: Find group header rows (above detail headers) --
    # Contains "Current Period", "Year To Date", "Annual" etc.
    # Scan up to 5 lines above to handle multi-line group headers.
    group_header_texts: list[tuple[float, str]] = []
    if header_line_idx is not None and header_line_idx > 0:
        for check_idx in range(max(0, header_line_idx - 5), header_line_idx):
            _, words = lines[check_idx]
            for w in words:
                t = w["text"].lower()
                if t in ("current", "year", "annual"):
                    group_header_texts.append((w["x0"], w["text"]))

    # -- Step 3: Build synthesized header rows for detect_columns --
    num_cols = 2 + len(col_centers)  # col A, col B, then data columns
    rows = []

    # -- Step 3a: Include pre-header title lines (HOA name, date range) --
    # These contain date text like "12/1/2025 - 12/31/2025" needed for growth factor inference.
    # Title lines appear before the group headers (~3 lines above detail header).
    if header_line_idx is not None:
        title_end = max(0, header_line_idx - 3)  # group headers are within 3 lines of detail header
        for pre_idx in range(0, title_end):
            _, pre_words = lines[pre_idx]
            pre_text = " ".join(w["text"] for w in pre_words)
            if pre_text.strip():
                pre_row = [None] * num_cols
                pre_row[0] = pre_text
                rows.append(pre_row)

    # Group header row — place at the FIRST (leftmost) column of each group.
    # Group headers are visually centered over their columns, so we find
    # the leftmost col_center within 80px to the left of the group header x0.
    group_row = [None] * num_cols
    for gx0, gtext in group_header_texts:
        # Find the leftmost column center that's within range (group headers sit over their columns)
        best_col = None
        for i, cx0 in enumerate(col_centers):
            if cx0 >= gx0 - 80 and cx0 <= gx0 + 80:
                if best_col is None or i < best_col:
                    best_col = i
        if best_col is None:
            best_col = min(range(len(col_centers)), key=lambda i: abs(col_centers[i] - gx0))
        if gtext.lower() == "current":
            group_row[2 + best_col] = "Current Period"
        elif gtext.lower() == "year":
            group_row[2 + best_col] = "Year To Date"
        elif gtext.lower() == "annual":
            group_row[2 + best_col] = "Annual Budget"
    rows.append(group_row)

    # Detail header row
    detail_row = [None] * num_cols
    for i, (_, text) in enumerate(header_x0s):
        detail_row[2 + i] = text
    rows.append(detail_row)

    # -- Step 4: Convert data/section lines to rows --
    _numeric_pat = re.compile(r"^[\$\(\)\-\d,\.]+$")

    for line_idx, (first_x0, word_list) in enumerate(lines):
        # Skip header lines (already synthesized above)
        if header_line_idx is not None and line_idx <= header_line_idx:
            continue
        # Skip group header lines
        if line_idx < (header_line_idx or 0):
            continue

        row = [None] * num_cols

        # Separate label words (left of data area) from value words
        label_words = []
        value_words = []
        for w in sorted(word_list, key=lambda w: w["x0"]):
            if w["x0"] < label_x_max:
                label_words.append(w)
            else:
                value_words.append(w)

        # Place label
        label_text = " ".join(w["text"] for w in label_words) if label_words else None
        if label_text:
            if first_x0 < _PDF_SECTION_X0_THRESHOLD:
                row[0] = label_text  # Section header → col A
            else:
                row[1] = label_text  # Line item → col B

        # Map each value word to the nearest column center
        for w in value_words:
            best_col = min(range(len(col_centers)), key=lambda i: abs(col_centers[i] - w["x0"]))
            text = w["text"].strip()
            if _numeric_pat.match(text) or text == "-":
                row[2 + best_col] = _parse_financial_float(text)
            elif row[2 + best_col] is None:
                row[2 + best_col] = text

        rows.append(row)
    return rows


def _read_pdf_rows(path: str) -> list:
    """Read text-based PDF income statement into normalized list of lists.

    Uses pdfplumber for character-level x0 coordinate extraction.
    Multi-page PDFs are stitched together.

    Raises:
        ValueError: If PDF has no text layer (< 20 words on page 1 = scanned).
    """
    import pdfplumber

    all_lines: list[tuple[float, list]] = []

    with pdfplumber.open(path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            words = page.extract_words(
                x_tolerance=3,
                y_tolerance=3,
                keep_blank_chars=False,
            )

            if page_num == 0 and len(words) < 20:
                raise ValueError(
                    "PDF has no text layer. Only text-based income statements are supported. "
                    "Scanned PDFs require OCR (not implemented). "
                    "Please upload a text-based .xlsx, .xls, or text PDF file."
                )

            # Group words into visual lines by y-coordinate (snapped to 2px grid)
            lines_by_y: dict[int, list] = {}
            for w in words:
                y_key = round(w["top"] / 2) * 2
                lines_by_y.setdefault(y_key, []).append(w)

            for y_key in sorted(lines_by_y.keys()):
                line_words = sorted(lines_by_y[y_key], key=lambda w: w["x0"])
                first_x0 = line_words[0]["x0"]
                all_lines.append((first_x0, line_words))

    return _pdf_lines_to_rows(all_lines)


def _read_rows(path: str, sheet_name: str = "Income Statement") -> list:
    """Dispatch to the appropriate format reader based on file extension.

    Supports:
        .xlsx / .xlsm  -> openpyxl reader
        .xls           -> xlrd reader
        .pdf           -> pdfplumber reader

    Raises:
        ValueError: For unsupported file extensions.
    """
    ext = Path(path).suffix.lower()
    if ext == ".xls":
        return _read_xls_rows(path, sheet_name)
    elif ext in (".xlsx", ".xlsm"):
        return _read_xlsx_rows(path, sheet_name)
    elif ext == ".pdf":
        return _read_pdf_rows(path)
    else:
        raise ValueError(
            f"Unsupported file format: {ext!r}. Supported formats: .xlsx, .xls, .pdf"
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_income_statement(
    path: str,
    sheet_name: str = "Income Statement",
) -> list:
    """Parse an income statement file (.xlsx, .xls, or .pdf) into classified line items.

    This is the main entry point. It:
    1. Reads the file using the appropriate format reader (_read_rows)
    2. Auto-detects column positions from headers (with LLM fallback)
    3. Walks rows with the section state machine
    4. Returns classified, validated line items

    Section classification is position-based, not keyword-based:
    - Items under "Operating Expense" -> category="operating" (including "reserve" labels)
    - Items under "Reserve Expenses (Per Reserve Study)" -> category="reserve", read_only=True
    - Items under "Operating Income" -> category="income"
    - Items under "Reserve Income" -> category="reserve", read_only=False

    Args:
        path: Absolute path to the income statement file.
        sheet_name: Worksheet name for Excel files (default "Income Statement").

    Returns:
        List of line item dicts, each with:
        {line_item_key, account_code, category, label, ytd_actual, annual_budget,
         projection, percent_change, read_only, section, validation_warning, raw}

    Raises:
        ValueError: For unsupported formats or scanned PDFs.
    """
    rows = _read_rows(path, sheet_name)
    col_indices = detect_columns(rows)
    return parse_rows_with_sections(rows, col_indices)
