"""H13 — hidden rows/columns are masked (blanked), not deleted.

Hidden rows must not become phantom line items, and a hidden column must not
shift positional column detection — while original row/column indices stay
aligned (masking, not deletion). A mostly-hidden sheet is logged prominently.
"""
from __future__ import annotations

import logging
from pathlib import Path

from openpyxl import Workbook

from app.services.income_statement_parser import _read_xlsx_rows


def _write(path: Path, rows, *, hidden_rows=(), hidden_cols=()) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Income Statement"
    for r, row in enumerate(rows, start=1):
        for c, val in enumerate(row, start=1):
            ws.cell(row=r, column=c, value=val)
    for r in hidden_rows:
        ws.row_dimensions[r].hidden = True
    for letter in hidden_cols:
        ws.column_dimensions[letter].hidden = True
    wb.save(str(path))


def test_hidden_row_is_blanked_not_a_phantom_line_item(tmp_path):
    path = tmp_path / "hidden_rows.xlsx"
    _write(
        path,
        [
            ["Label", "Annual"],
            ["Management", 1200],
            ["SCRATCH SUBTOTAL", 99999],  # hidden helper row
            ["Insurance", 800],
        ],
        hidden_rows=[3],
    )
    rows = _read_xlsx_rows(str(path))
    # Row count preserved (alignment); the hidden row is fully blanked.
    assert len(rows) == 4
    assert rows[2] == [None, None]
    labels = [r[0] for r in rows]
    assert "SCRATCH SUBTOTAL" not in labels


def test_hidden_column_masked_preserves_alignment_of_visible_columns(tmp_path):
    path = tmp_path / "hidden_col.xlsx"
    # Column B is a hidden scratch column between the label (A) and annual (C).
    _write(
        path,
        [
            ["Label", "Scratch", "Annual"],
            ["Management", 111, 1200],
            ["Insurance", 222, 800],
        ],
        hidden_cols=["B"],
    )
    rows = _read_xlsx_rows(str(path))
    # Indices are preserved (3 columns); the hidden column is blanked, so the
    # annual figure is still at its original index 2 — not shifted.
    assert rows[1][0] == "Management"
    assert rows[1][1] is None
    assert rows[1][2] == 1200
    assert rows[2][2] == 800


def test_mostly_hidden_sheet_is_logged(tmp_path, caplog):
    path = tmp_path / "mostly_hidden.xlsx"
    _write(
        path,
        [
            ["Label", "Annual"],
            ["A", 1],
            ["B", 2],
            ["C", 3],
            ["D", 4],
        ],
        hidden_rows=[2, 3, 4],  # 3 of 4 data rows hidden
    )
    with caplog.at_level(logging.WARNING):
        _read_xlsx_rows(str(path))
    assert any("HIDDEN" in rec.message or "hidden" in rec.message for rec in caplog.records)


def test_no_hidden_cells_unchanged(tmp_path):
    path = tmp_path / "plain.xlsx"
    _write(
        path,
        [["Label", "Annual"], ["Management", 1200], ["Insurance", 800]],
    )
    rows = _read_xlsx_rows(str(path))
    assert rows[1] == ["Management", 1200]
    assert rows[2] == ["Insurance", 800]
