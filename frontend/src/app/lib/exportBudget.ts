import XLSXStyle from 'xlsx-js-style';
import { calcProposed, calcMonthly, calcCategoryTotal, getCategoryLabel } from './budget';
import type { LineItem } from '../data/mockData';

// ── Style constants ────────────────────────────────────────────────────────────

const border = {
  top:    { style: 'thin', color: { rgb: 'CCCCCC' } },
  bottom: { style: 'thin', color: { rgb: 'CCCCCC' } },
  left:   { style: 'thin', color: { rgb: 'CCCCCC' } },
  right:  { style: 'thin', color: { rgb: 'CCCCCC' } },
};

// ── Cell factory ───────────────────────────────────────────────────────────────

type CellType = 'n' | 's' | 'z';

interface XlsxCell {
  v: number | string;
  t: CellType;
  z?: string;
  s?: Record<string, unknown>;
}

function makeCell(
  value: number | string,
  format?: string,
  styleOverrides?: Record<string, unknown>,
): XlsxCell {
  const t: CellType =
    value === '' ? 'z' : typeof value === 'number' ? 'n' : 's';

  const cell: XlsxCell = {
    v: value,
    t,
    s: {
      border,
      ...styleOverrides,
    },
  };

  if (format) {
    cell.z = format;
  }

  return cell;
}

// ── Main export function ───────────────────────────────────────────────────────

export function exportEnrichedBudget(lineItems: LineItem[], hoaName: string): void {
  const rows: XlsxCell[][] = [];
  const merges: { s: { r: number; c: number }; e: { r: number; c: number } }[] = [];

  // ── Row 0: Title ────────────────────────────────────────────────────────────
  const titleCell = makeCell(
    `${hoaName} — Proposed Budget`,
    undefined,
    {
      font: { bold: true, sz: 14 },
      alignment: { horizontal: 'center' },
    },
  );
  rows.push([
    titleCell,
    makeCell(''), makeCell(''), makeCell(''),
    makeCell(''), makeCell(''), makeCell(''), makeCell(''),
  ]);
  merges.push({ s: { r: 0, c: 0 }, e: { r: 0, c: 7 } });

  // ── Row 1: Subtitle ─────────────────────────────────────────────────────────
  const exportDate = new Date().toLocaleDateString('en-US', {
    year: 'numeric', month: 'long', day: 'numeric',
  });
  const subtitleCell = makeCell(
    `Exported: ${exportDate}`,
    undefined,
    {
      font: { italic: true, color: { rgb: '888888' } },
      alignment: { horizontal: 'center' },
    },
  );
  rows.push([
    subtitleCell,
    makeCell(''), makeCell(''), makeCell(''),
    makeCell(''), makeCell(''), makeCell(''), makeCell(''),
  ]);
  merges.push({ s: { r: 1, c: 0 }, e: { r: 1, c: 7 } });

  // ── Row 2: Blank spacer ─────────────────────────────────────────────────────
  rows.push([
    makeCell(''), makeCell(''), makeCell(''), makeCell(''),
    makeCell(''), makeCell(''), makeCell(''), makeCell(''),
  ]);

  // ── Row 3: Column headers ───────────────────────────────────────────────────
  const headerStyle = {
    font: { bold: true },
    fill: { fgColor: { rgb: 'D9D9D9' } },
    border,
    alignment: { horizontal: 'center' },
  };
  const headers = [
    'Account Code', 'Line Item', 'YTD Actual', 'Annual Budget',
    '% Change', 'Proposed Budget', 'Monthly', 'Notes',
  ];
  rows.push(
    headers.map((h) => makeCell(h, undefined, headerStyle)),
  );

  // ── Category sections ───────────────────────────────────────────────────────
  const categories: Array<'income' | 'operating' | 'reserve'> = ['income', 'operating', 'reserve'];

  for (const cat of categories) {
    const catItems = lineItems.filter((item) => item.category === cat);

    // Category header row
    const catHeaderRowIdx = rows.length;
    const catHeaderCell = makeCell(
      getCategoryLabel(cat),
      undefined,
      {
        font: { bold: true },
        fill: { fgColor: { rgb: 'BFBFBF' } },
        alignment: { horizontal: 'left' },
      },
    );
    rows.push([
      catHeaderCell,
      makeCell('', undefined, { fill: { fgColor: { rgb: 'BFBFBF' } } }),
      makeCell('', undefined, { fill: { fgColor: { rgb: 'BFBFBF' } } }),
      makeCell('', undefined, { fill: { fgColor: { rgb: 'BFBFBF' } } }),
      makeCell('', undefined, { fill: { fgColor: { rgb: 'BFBFBF' } } }),
      makeCell('', undefined, { fill: { fgColor: { rgb: 'BFBFBF' } } }),
      makeCell('', undefined, { fill: { fgColor: { rgb: 'BFBFBF' } } }),
      makeCell('', undefined, { fill: { fgColor: { rgb: 'BFBFBF' } } }),
    ]);
    merges.push({ s: { r: catHeaderRowIdx, c: 0 }, e: { r: catHeaderRowIdx, c: 7 } });

    // Data rows
    for (const item of catItems) {
      const proposed = calcProposed(item.annualBudget, item.percentChange);
      const monthly  = calcMonthly(proposed);
      const isRO     = item.readOnly === true;
      const nameStyle = isRO ? { font: { italic: true } } : undefined;

      rows.push([
        makeCell(item.accountCode ?? ''),
        makeCell(item.name, undefined, nameStyle),
        isRO ? makeCell('')        : makeCell(item.ytdActual,        '$#,##0'),
        isRO ? makeCell('')        : makeCell(item.annualBudget,     '$#,##0'),
        isRO ? makeCell('')        : makeCell(item.percentChange / 100, '0.00%'),
        isRO ? makeCell('')        : makeCell(proposed,              '$#,##0'),
        isRO ? makeCell('')        : makeCell(monthly,               '$#,##0.00'),
        makeCell(item.note?.body ?? ''),
      ]);
    }

    // Subtotal row
    const subtotalStyle = { font: { bold: true } };
    rows.push([
      makeCell(''),
      makeCell('Subtotal', undefined, subtotalStyle),
      makeCell(calcCategoryTotal(catItems, 'ytdActual'),      '$#,##0',    subtotalStyle),
      makeCell(calcCategoryTotal(catItems, 'annualBudget'),   '$#,##0',    subtotalStyle),
      makeCell(''),
      makeCell(calcCategoryTotal(catItems, 'proposedChange'), '$#,##0',    subtotalStyle),
      makeCell(calcCategoryTotal(catItems, 'monthly'),        '$#,##0.00', subtotalStyle),
      makeCell(''),
    ]);

    // Blank spacer between categories
    rows.push([
      makeCell(''), makeCell(''), makeCell(''), makeCell(''),
      makeCell(''), makeCell(''), makeCell(''), makeCell(''),
    ]);
  }

  // ── Grand Total row ─────────────────────────────────────────────────────────
  const opItems  = lineItems.filter((i) => i.category === 'operating');
  const resItems = lineItems.filter((i) => i.category === 'reserve');

  const totalProposed =
    calcCategoryTotal(opItems,  'proposedChange') +
    calcCategoryTotal(resItems, 'proposedChange');
  const totalMonthly =
    calcCategoryTotal(opItems,  'monthly') +
    calcCategoryTotal(resItems, 'monthly');

  const grandStyle = {
    font: { bold: true, color: { rgb: 'FFFFFF' } },
    fill: { fgColor: { rgb: '000000' } },
  };

  rows.push([
    makeCell('', undefined, grandStyle),
    makeCell('GRAND TOTAL', undefined, grandStyle),
    makeCell('', undefined, grandStyle),
    makeCell('', undefined, grandStyle),
    makeCell('', undefined, grandStyle),
    makeCell(totalProposed, '$#,##0',    grandStyle),
    makeCell(totalMonthly,  '$#,##0.00', grandStyle),
    makeCell('', undefined, grandStyle),
  ]);

  // ── Build worksheet ─────────────────────────────────────────────────────────
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const ws: any = XLSXStyle.utils.aoa_to_sheet(rows as any);

  // Column widths
  ws['!cols'] = [
    { wch: 14 }, { wch: 42 }, { wch: 16 }, { wch: 16 },
    { wch: 11 }, { wch: 18 }, { wch: 14 }, { wch: 40 },
  ];

  // Merges
  ws['!merges'] = merges;

  // Apply cell styles & formats from our cell objects back onto the sheet cells.
  // aoa_to_sheet sets the values but we need to re-apply .s and .z because
  // xlsx-js-style only reads styles from cell objects that are set on ws directly.
  const totalRows = rows.length;
  for (let r = 0; r < totalRows; r++) {
    const row = rows[r];
    for (let c = 0; c < row.length; c++) {
      const cellRef = XLSXStyle.utils.encode_cell({ r, c });
      const srcCell = row[c];
      if (!ws[cellRef]) {
        // aoa_to_sheet may skip truly empty cells; create a placeholder
        ws[cellRef] = { v: '', t: 'z' };
      }
      if (srcCell.s) {
        ws[cellRef].s = srcCell.s;
      }
      if (srcCell.z) {
        ws[cellRef].z = srcCell.z;
      }
    }
  }

  // ── Workbook & download ─────────────────────────────────────────────────────
  const wb = XLSXStyle.utils.book_new();
  XLSXStyle.utils.book_append_sheet(wb, ws, 'Budget');

  const safe = hoaName.replace(/[^a-zA-Z0-9_ -]/g, '').trim().replace(/\s+/g, '_');
  const filename = `${safe || 'Budget'}_${new Date().getFullYear()}_proposed.xlsx`;

  XLSXStyle.writeFile(wb, filename);
}
