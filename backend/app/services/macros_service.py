"""Core macro-transformation functions.

Each function is implemented to be deterministic and does not rely on Excel UI state
(ActiveCell/Selection). Functions operate on an on-disk workbook path and return
JSON-serializable Python structures.
"""
from typing import Any, Dict
from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
import zipfile
import tempfile
import shutil
import os
import re
import datetime

from app.config import settings


def _ensure_temp_dir():
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    return settings.TEMP_DIR


def read_sheet_as_table(path: str, sheet: str, header_row: int = 1) -> Dict[str, Any]:
    """Read a worksheet into headers and rows (skip empty rows).

    Args:
        path: path to workbook (.xlsx)
        sheet: worksheet name
        header_row: 1-based row index containing headers (default 1)

    Returns:
        Dict with keys: sheet, headers, rows
    """
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
        return {"sheet": sheet, "headers": headers, "rows": rows}
    finally:
        wb.close()


def payment_search_format(path: str, sheet: str = "Sheet1") -> Dict[str, Any]:
    """Sort rows by column F then D and format column I as d-mmm (for JSON).

    Returns headers/rows dict.
    """
    table = read_sheet_as_table(path, sheet)
    rows = table["rows"]

    def get_col(row, idx):
        if idx - 1 < len(row):
            return row[idx - 1]
        return None

    def key_fn(r):
        f = get_col(r, 6)
        d = get_col(r, 4)
        return ((f or ""), (d or ""))

    rows_sorted = sorted(rows, key=key_fn)

    # format column I if datetime
    formatted = []
    for row in rows_sorted:
        new = list(row)
        v = get_col(row, 9)
        if isinstance(v, (datetime.date, datetime.datetime)):
            new[8] = v.strftime("%d-%b")
        formatted.append(new)

    return {"sheet": sheet, "headers": table["headers"], "rows": formatted}


def sum_and_format(path: str, sheet: str, start_cell: str, row_count: int, target_col: str = "H") -> Dict[str, Any]:
    """Compute the sum of values in column left of target_col from start_row to start_row+row_count.

    Writes the computed total into target_col at start_row (modifies file).
    Returns the sum and the cell address.
    """
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
        for r in range(start_row, start_row + row_count + 1):
            v = ws.cell(row=r, column=src_idx).value
            if v is None:
                continue
            try:
                total += float(str(v).replace(',', '').replace('$', ''))
            except Exception:
                continue

        ws.cell(row=start_row, column=target_idx, value=round(total, 2))
        wb.save(path)
        return {"sheet": sheet, "sum_cell": f"{target_col}{start_row}", "sum": round(total, 2)}
    finally:
        wb.close()


def ach_sum(path: str, sheet: str, start_cell: str, find_text: str = "ACH Draft") -> Dict[str, Any]:
    """Find last cell containing find_text and sum values from start_row to that row in column left of start_cell.

    Writes sum into start_cell's column at start_row and returns metadata.
    """
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
        return {"sheet": sheet, "sum_cell": f"{col_letter}{start_row}", "sum": round(total, 2), "start_row": start_row, "last_found_row": last}
    finally:
        wb.close()


def one_cell(path: str, sheet: str, start_cell: str, copy_from_offset: int = -3, copy_to_offset: int = 5) -> Dict[str, Any]:
    """Copy value from start_cell+copy_from_offset to start_cell+copy_to_offset."""
    col_letter, row = coordinate_from_string(start_cell)
    start_idx = column_index_from_string(col_letter)
    wb = load_workbook(path)
    try:
        ws = wb[sheet]
        src = ws.cell(row=row, column=start_idx + copy_from_offset).value
        ws.cell(row=row, column=start_idx + copy_to_offset, value=src)
        wb.save(path)
        return {"from": f"{start_idx + copy_from_offset},{row}", "to": f"{start_idx + copy_to_offset},{row}", "value": src}
    finally:
        wb.close()


def find_dups(path: str, sheet: str, lookup_col: str = 'F') -> Dict[str, Any]:
    """Return list of duplicate values in lookup_col (based on first appearance).

    Does not modify workbook; just returns duplicates.
    """
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
        return {"duplicates": dups}
    finally:
        wb.close()


def cumulative_sum(path: str, sheet: str, start_row: int, start_col: int, end_row: int) -> Dict[str, Any]:
    """Sum values from start_row..end_row-1 in column start_col (1-based).

    Does not modify workbook.
    """
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
        return {"cumulative_sum": round(total, 2), "start_row": start_row, "end_row": end_row - 1}
    finally:
        wb.close()


def remove_protection_return_bytes(path: str) -> bytes:
    """Return unlocked workbook bytes by editing zipped xml to remove protection tags.

    This function will not save any persistent unlocked file; it returns bytes which can
    be returned as a download response.
    """
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

        # Repack into bytes under temp dir
        out_path = os.path.join(tmp_base, f"unlocked_{os.path.basename(path)}")
        with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(tmpdir):
                for f in files:
                    full = os.path.join(root, f)
                    arc = os.path.relpath(full, tmpdir)
                    z.write(full, arc)

        with open(out_path, 'rb') as f:
            data = f.read()
        try:
            os.remove(out_path)
        except Exception:
            pass
        return data
    finally:
        shutil.rmtree(tmpdir)


def run_all_macros_pipeline(path: str, sheet: str = "Income Statement") -> Dict[str, Any]:
    """Convenience function running a subset of available macro operations and returning combined result.

    This is intentionally lightweight and does not attempt to fully emulate the interactive VBA behavior.
    """
    res = {}
    try:
        res['payment_search'] = payment_search_format(path, 'Sheet1') if 'Sheet1' in load_workbook(path, read_only=True).sheetnames else None
    except Exception as e:
        res['payment_search_error'] = str(e)

    # Add placeholders for others if needed
    res['note'] = 'Run subset of macros; use dedicated endpoints for granular operations.'
    return res

