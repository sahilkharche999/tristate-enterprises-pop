"""Core macro-transformation functions.

Each function is implemented to be deterministic and does not rely on Excel UI state
(ActiveCell/Selection). Functions operate on an on-disk workbook path and return
JSON-serializable Python structures.
"""
import datetime
import os
import re
import shutil
import tempfile
import zipfile
from typing import Any, Dict
import logging
import time

from ..config import settings
from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_from_string, column_index_from_string


_AM_COL = column_index_from_string("AM")  # column 39, pre-computed once


def _ensure_temp_dir():
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    return settings.TEMP_DIR


# module logger
logger = logging.getLogger(__name__)


def read_sheet_as_table(path: str, sheet: str, header_row: int = 1) -> Dict[str, Any]:
    """Read a worksheet into headers and rows (skip empty rows).

    Args:
        path: path to workbook (.xlsx)
        sheet: worksheet name
        header_row: 1-based row index containing headers (default 1)

    Returns:
        Dict with keys: sheet, headers, rows
    """
    start = time.perf_counter()
    logger.info("read_sheet_as_table start: path=%s sheet=%s header_row=%s", path, sheet, header_row)
    wb = load_workbook(path, data_only=True)
    try:
        if sheet not in wb.sheetnames:
            raise ValueError(f"Sheet not found: {sheet}")
        ws = wb[sheet]
        max_col = ws.max_column
        headers = []
        if header_row <= ws.max_row:
            for c in range(1, max_col + 1):
                headers.append(ws.cell(row=header_row, column=c).value)
        else:
            headers = [None] * max_col

        rows = []
        for r in range(header_row + 1, ws.max_row + 1):
            row = [ws.cell(row=r, column=c).value for c in range(1, max_col + 1)]
            if any(v is not None for v in row):
                rows.append(row)
        result = {"sheet": sheet, "headers": headers, "rows": rows}
        duration = time.perf_counter() - start
        logger.info("read_sheet_as_table done: path=%s sheet=%s rows=%d headers=%d duration=%.3fs", path, sheet, len(rows), len(headers), duration)
        return result
    finally:
        wb.close()


def _coerce_for_sort(v):
    """Coerce a cell value to a comparable string for stable sorting.

    Dates become ISO strings, None becomes empty string, otherwise str(v).
    """
    if v is None:
        return ""
    if isinstance(v, (datetime.date, datetime.datetime)):
        # Use ISO format for consistent ordering
        return v.isoformat()
    return str(v)


def payment_search_format(path: str, sheet: str = "Sheet1") -> Dict[str, Any]:
    """Sort rows by column F then D and format column I as d-mmm (for JSON).

    Returns headers/rows dict.
    """
    start = time.perf_counter()
    logger.info("payment_search_format start: path=%s sheet=%s", path, sheet)
    table = read_sheet_as_table(path, sheet)
    rows = table["rows"]

    def get_col(row, idx):
        if idx - 1 < len(row):
            return row[idx - 1]
        return None

    def key_fn(r):
        f = get_col(r, 6)
        d = get_col(r, 4)
        # Coerce values to stable comparable strings to avoid TypeError when
        # mixed types (dates/numbers/strings) appear in the same column.
        return (_coerce_for_sort(f), _coerce_for_sort(d))

    rows_sorted = sorted(rows, key=key_fn)

    # format column I if datetime
    formatted = []
    for row in rows_sorted:
        new = list(row)
        v = get_col(row, 9)
        if isinstance(v, (datetime.date, datetime.datetime)):
            new[8] = v.strftime("%d-%b")
        formatted.append(new)

    duration = time.perf_counter() - start
    logger.info("payment_search_format done: path=%s sheet=%s in_rows=%d out_rows=%d duration=%.3fs", path, sheet, len(rows), len(formatted), duration)
    return {"sheet": sheet, "headers": table["headers"], "rows": formatted}


def sum_and_format(path: str, sheet: str, start_cell: str, row_count: int, target_col: str = "H") -> Dict[str, Any]:
    """Compute the sum of values in column left of target_col from start_row for `row_count` rows.

    Writes the computed total into target_col at start_row (modifies file).
    Returns the sum and the cell address.
    """
    start = time.perf_counter()
    logger.info("sum_and_format start: path=%s sheet=%s start_cell=%s row_count=%s target_col=%s", path, sheet, start_cell, row_count, target_col)
    col_letter, row = coordinate_from_string(start_cell)
    start_row = row
    target_idx = column_index_from_string(target_col)
    src_idx = target_idx - 1

    wb = load_workbook(path)
    try:
        if sheet not in wb.sheetnames:
            raise ValueError("Sheet not found: " + sheet)
        ws = wb[sheet]
        total = 0.0
        # Iterate over exactly `row_count` rows starting at start_row
        for r in range(start_row, start_row + row_count):
            v = ws.cell(row=r, column=src_idx).value
            if v is None:
                continue
            try:
                total += float(str(v).replace(',', '').replace('$', ''))
            except Exception:
                continue

        ws.cell(row=start_row, column=target_idx, value=round(total, 2))
        wb.save(path)
        duration = time.perf_counter() - start
        logger.info("sum_and_format done: path=%s sheet=%s sum=%.2f sum_cell=%s duration=%.3fs", path, sheet, round(total, 2), f"{target_col}{start_row}", duration)
        return {"sheet": sheet, "sum_cell": f"{target_col}{start_row}", "sum": round(total, 2)}
    finally:
        wb.close()


def ach_sum(path: str, sheet: str, start_cell: str, find_text: str = "ACH Draft") -> Dict[str, Any]:
    """Find last cell containing find_text and sum values from start_row to that row in column left of start_cell.

    Writes sum into start_cell's column at start_row and returns metadata.
    """
    start = time.perf_counter()
    logger.info("ach_sum start: path=%s sheet=%s start_cell=%s find_text=%s", path, sheet, start_cell, find_text)
    col_letter, row = coordinate_from_string(start_cell)
    start_row = row
    start_idx = column_index_from_string(col_letter)
    src_idx = start_idx - 1

    wb = load_workbook(path)
    try:
        ws = wb[sheet]
        last = None
        for r in range(1, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                v = ws.cell(row=r, column=c).value
                if isinstance(v, str) and find_text in v:
                    last = r
        if last is None:
            raise ValueError(f"Text '{find_text}' not found in sheet {sheet}")

        total = 0.0
        for r in range(start_row, last + 1):
            v = ws.cell(row=r, column=src_idx).value
            if v is None:
                continue
            try:
                total += float(str(v).replace(',', '').replace('$', ''))
            except Exception:
                continue

        ws.cell(row=start_row, column=start_idx, value=round(total, 2))
        wb.save(path)
        duration = time.perf_counter() - start
        logger.info("ach_sum done: path=%s sheet=%s sum=%.2f start_row=%d last_found_row=%d duration=%.3fs", path, sheet, round(total, 2), start_row, last, duration)
        return {"sheet": sheet, "sum_cell": f"{col_letter}{start_row}", "sum": round(total, 2), "start_row": start_row,
                "last_found_row": last}
    finally:
        wb.close()


def one_cell(path: str, sheet: str, start_cell: str, copy_from_offset: int = -3, copy_to_offset: int = 5) -> Dict[
    str, Any]:
    """Copy value from start_cell+copy_from_offset to start_cell+copy_to_offset."""
    start = time.perf_counter()
    logger.info("one_cell start: path=%s sheet=%s start_cell=%s from_offset=%s to_offset=%s", path, sheet, start_cell, copy_from_offset, copy_to_offset)
    col_letter, row = coordinate_from_string(start_cell)
    start_idx = column_index_from_string(col_letter)
    wb = load_workbook(path)
    try:
        ws = wb[sheet]
        src = ws.cell(row=row, column=start_idx + copy_from_offset).value
        ws.cell(row=row, column=start_idx + copy_to_offset, value=src)
        wb.save(path)
        duration = time.perf_counter() - start
        logger.info("one_cell done: path=%s sheet=%s value=%r duration=%.3fs", path, sheet, src, duration)
        return {"from": f"{start_idx + copy_from_offset},{row}", "to": f"{start_idx + copy_to_offset},{row}",
                "value": src}
    finally:
        wb.close()


def find_dups(path: str, sheet: str, lookup_col: str = 'F') -> Dict[str, Any]:
    """Return list of duplicate values in lookup_col (based on first appearance).

    Does not modify workbook; just returns duplicates.
    """
    start = time.perf_counter()
    logger.info("find_dups start: path=%s sheet=%s lookup_col=%s", path, sheet, lookup_col)
    idx = column_index_from_string(lookup_col)
    wb = load_workbook(path, data_only=True)
    try:
        ws = wb[sheet]
        seen = set()
        dups = []
        for r in range(2, ws.max_row + 1):
            v = ws.cell(row=r, column=idx).value
            if v is None:
                continue
            if v in seen:
                dups.append({"row": r, "value": v})
            else:
                seen.add(v)
        duration = time.perf_counter() - start
        logger.info("find_dups done: path=%s sheet=%s duplicates=%d duration=%.3fs", path, sheet, len(dups), duration)
        return {"duplicates": dups}
    finally:
        wb.close()


def cumulative_sum(path: str, sheet: str, start_row: int, start_col: int, end_row: int) -> Dict[str, Any]:
    """Sum values from start_row..end_row-1 in column start_col (1-based).

    Does not modify workbook.
    """
    start = time.perf_counter()
    logger.info("cumulative_sum start: path=%s sheet=%s start_row=%s start_col=%s end_row=%s", path, sheet, start_row, start_col, end_row)
    wb = load_workbook(path, data_only=True)
    try:
        ws = wb[sheet]
        total = 0.0
        for r in range(start_row, end_row):
            v = ws.cell(row=r, column=start_col).value
            if v is None:
                continue
            try:
                total += float(str(v).replace(',', '').replace('$', ''))
            except Exception:
                continue
        duration = time.perf_counter() - start
        logger.info("cumulative_sum done: path=%s sheet=%s sum=%.2f duration=%.3fs", path, sheet, round(total, 2), duration)
        return {"cumulative_sum": round(total, 2), "start_row": start_row, "end_row": end_row - 1}
    finally:
        wb.close()


def read_first_sheet_preview(path: str, max_rows: int) -> Dict[str, Any]:
    """Read the first worksheet up to max_rows, returning {sheet, headers, rows}."""
    wb = load_workbook(path, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        max_col = ws.max_column
        headers = [ws.cell(row=1, column=c).value for c in range(1, max_col + 1)]
        rows = []
        for r in range(2, min(ws.max_row, max_rows) + 1):
            row = [ws.cell(row=r, column=c).value for c in range(1, max_col + 1)]
            if any(v is not None for v in row):
                rows.append(row)
        return {"sheet": ws.title, "headers": headers, "rows": rows}
    finally:
        wb.close()


def write_percent_changes_by_label(path: str, sheet: str, changes: Dict[str, float]) -> None:
    """Write decimal percent change values to column AM for rows whose col B label matches.

    Args:
        path: path to workbook (.xlsx)
        sheet: worksheet name (typically "Income Statement")
        changes: mapping of label → decimal percent change (e.g. {"Insurance": 0.085})
    """
    if not changes:
        return
    wb = load_workbook(path)
    try:
        if sheet not in wb.sheetnames:
            raise ValueError(f"Sheet not found: {sheet}")
        ws = wb[sheet]
        for r in range(1, ws.max_row + 1):
            label = ws.cell(row=r, column=2).value  # col B
            if label is not None and str(label).strip() in changes:
                ws.cell(row=r, column=_AM_COL, value=changes[str(label).strip()])
        wb.save(path)
    finally:
        wb.close()


def remove_protection_return_bytes(path: str) -> bytes:
    """Return unlocked workbook bytes by editing zipped xml to remove protection tags.

    This function will not save any persistent unlocked file; it returns bytes which can
    be returned as a download response.
    """
    start = time.perf_counter()
    logger.info("remove_protection_return_bytes start: path=%s", path)
    if not os.path.exists(path):
        raise FileNotFoundError("File not found: " + path)

    # Use configured temp directory if available
    tmp_base = _ensure_temp_dir()
    tmpdir = tempfile.mkdtemp(prefix="unlockxlsx_", dir=tmp_base)
    try:
        with zipfile.ZipFile(path, 'r') as z:
            z.extractall(tmpdir)

        # Remove protection tags from worksheets
        sheets_dir = os.path.join(tmpdir, 'xl', 'worksheets')
        if os.path.isdir(sheets_dir):
            for fn in os.listdir(sheets_dir):
                fpath = os.path.join(sheets_dir, fn)
                with open(fpath, 'r', encoding='utf-8') as fp:
                    txt = fp.read()
                txt = re.sub(r'<sheetProtection[^/>]*/>', '', txt)
                txt = re.sub(r'<sheetProtection[^>]*>.*?</sheetProtection>', '', txt, flags=re.DOTALL)
                with open(fpath, 'w', encoding='utf-8') as fp:
                    fp.write(txt)

        wb_xml = os.path.join(tmpdir, 'xl', 'workbook.xml')
        if os.path.exists(wb_xml):
            with open(wb_xml, 'r', encoding='utf-8') as fp:
                txt = fp.read()
            txt = re.sub(r'<workbookProtection[^/>]*/>', '', txt)
            txt = re.sub(r'<workbookProtection[^>]*>.*?</workbookProtection>', '', txt, flags=re.DOTALL)
            txt = re.sub(r'<fileSharing[^/>]*/>', '', txt)
            txt = re.sub(r'<fileSharing[^>]*>.*?</fileSharing>', '', txt, flags=re.DOTALL)
            with open(wb_xml, 'w', encoding='utf-8') as fp:
                fp.write(txt)

        # Repack into bytes under temp dir using a unique filename in the same tmpdir
        out_tmp = tempfile.NamedTemporaryFile(delete=False, dir=tmpdir, suffix=".xlsx")
        out_path = out_tmp.name
        out_tmp.close()

        with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(tmpdir):
                for f in files:
                    full = os.path.join(root, f)
                    # do not include the output file itself if it's in the same dir
                    if os.path.abspath(full) == os.path.abspath(out_path):
                        continue
                    arc = os.path.relpath(full, tmpdir)
                    z.write(full, arc)

        with open(out_path, 'rb') as f:
            data = f.read()
        try:
            os.remove(out_path)
        except Exception as e:
            logger.warning("Temp file cleanup failed: %s", e)
        duration = time.perf_counter() - start
        logger.info("remove_protection_return_bytes done: path=%s out_bytes=%d duration=%.3fs", path, len(data), duration)
        return data
    finally:
        shutil.rmtree(tmpdir)


def run_all_macros_pipeline(path: str, sheet: str = "Income Statement") -> Dict[str, Any]:
    """Convenience function running a subset of available macro operations and returning combined result.

    This is intentionally lightweight and does not attempt to fully emulate the interactive VBA behavior.
    """
    start = time.perf_counter()
    logger.info("run_all_macros_pipeline start: path=%s sheet=%s", path, sheet)
    res = {}
    try:
        wb = load_workbook(path, read_only=True)
        try:
            sheetnames = wb.sheetnames
        finally:
            wb.close()

        try:
            res['payment_search'] = payment_search_format(path, 'Sheet1') if 'Sheet1' in sheetnames else None
        except Exception as e:
            logger.exception("payment_search failed in pipeline for path=%s: %s", path, e)
            res['payment_search_error'] = str(e)
    except Exception as e:
        # If opening workbook fails, return the error
        logger.exception("run_all_macros_pipeline failed to open workbook: %s", e)
        res['error'] = str(e)

    # Add placeholders for others if needed
    res['note'] = 'Run subset of macros; use dedicated endpoints for granular operations.'
    duration = time.perf_counter() - start
    logger.info("run_all_macros_pipeline done: path=%s duration=%.3fs", path, duration)
    return res
