import XLSXStyle from 'xlsx-js-style';
import {
  calcDisplayProposed,
  calcDisplayMonthly,
  calcCategoryTotal,
  getCategoryLabel,
} from './budget';
import type { LineItem } from '../data/mockData';

// ── Types ─────────────────────────────────────────────────────────────────────

type CellType = 'n' | 's' | 'z';

interface XlsxCell {
  v: number | string;
  t: CellType;
  z?: string;
  s?: Record<string, unknown>;
}

// ── Style constants ────────────────────────────────────────────────────────────

const THIN_BORDER = {
  top:    { style: 'thin', color: { rgb: 'CCCCCC' } },
  bottom: { style: 'thin', color: { rgb: 'CCCCCC' } },
  left:   { style: 'thin', color: { rgb: 'CCCCCC' } },
  right:  { style: 'thin', color: { rgb: 'CCCCCC' } },
};

const STYLES = {
  title: {
    font: { bold: true, sz: 14 },
    alignment: { horizontal: 'center' },
  },
  subtitle: {
    font: { bold: true, sz: 11 },
    alignment: { horizontal: 'center' },
  },
  metaRow: {
    font: { italic: true, color: { rgb: '888888' } },
    alignment: { horizontal: 'center' },
  },
  sectionHeader: {
    font: { bold: true },
    fill: { fgColor: { rgb: 'BFBFBF' } },
    alignment: { horizontal: 'left' },
  },
  sectionHeaderEmpty: {
    fill: { fgColor: { rgb: 'BFBFBF' } },
  },
  columnHeader: {
    font: { bold: true },
    fill: { fgColor: { rgb: 'D9D9D9' } },
    border: THIN_BORDER,
    alignment: { horizontal: 'center' },
  },
  dataRow: {
    border: THIN_BORDER,
  },
  readOnlyRow: {
    font: { italic: true },
    border: THIN_BORDER,
  },
  totalRow: {
    font: { bold: true },
    fill: { fgColor: { rgb: 'EAF4E8' } },
    border: THIN_BORDER,
  },
  reserveRow: {
    font: { bold: true },
    fill: { fgColor: { rgb: 'FFF3CD' } },
    border: THIN_BORDER,
  },
  netRow: {
    font: { bold: true, color: { rgb: 'FFFFFF' } },
    fill: { fgColor: { rgb: '2E7D32' } },
    border: THIN_BORDER,
  },
  cashFlowRow: {
    font: { bold: true },
    fill: { fgColor: { rgb: 'DCEEFB' } },
    border: THIN_BORDER,
  },
  cumulativeRow: {
    font: { bold: true, italic: true },
    fill: { fgColor: { rgb: 'E8D5F5' } },
    border: THIN_BORDER,
  },
  grandTotal: {
    font: { bold: true, color: { rgb: 'FFFFFF' } },
    fill: { fgColor: { rgb: '000000' } },
    border: THIN_BORDER,
  },
};

// ── Cell factory ───────────────────────────────────────────────────────────────

function makeCell(
  value: number | string,
  format?: string,
  styleOverrides?: Record<string, unknown>,
): XlsxCell {
  const t: CellType =
    value === '' ? 'z' : typeof value === 'number' ? 'n' : 's';

  const cell: XlsxCell = { v: value, t };
  if (format) cell.z = format;
  if (styleOverrides) cell.s = styleOverrides;
  return cell;
}

// ── Helper utilities ──────────────────────────────────────────────────────────

export function getProposedValue(item: LineItem, reserveInflationRate?: number | null): number {
  return calcDisplayProposed(item, reserveInflationRate);
}

export function getReserveContributionLine(lineItems: LineItem[]): LineItem | null {
  return (
    lineItems.find((item) => item.reserveGroup === 'transfer') ??
    lineItems.find(
      (item) =>
        item.category === 'operating' &&
        /allocation|transfer/i.test(item.name),
    ) ??
    null
  );
}

export function getDisplayName(item: LineItem): string {
  if (item.mergedCount && item.mergedCount > 1) {
    return `${item.name} (merged)`;
  }
  return item.name;
}

// ── Worksheet builder utilities ───────────────────────────────────────────────

function applyStylesToSheet(
  ws: Record<string, unknown>,
  rows: XlsxCell[][],
): void {
  for (let r = 0; r < rows.length; r++) {
    const row = rows[r];
    for (let c = 0; c < row.length; c++) {
      const cellRef = XLSXStyle.utils.encode_cell({ r, c });
      const srcCell = row[c];
      if (!ws[cellRef]) ws[cellRef] = { v: '', t: 'z' };
      if (srcCell.s) (ws[cellRef] as Record<string, unknown>).s = srcCell.s;
      if (srcCell.z) (ws[cellRef] as Record<string, unknown>).z = srcCell.z;
    }
  }
}

// ── Proforma sheet builder (Sheet 1) ──────────────────────────────────────────

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const PROFORMA_COLS = 15; // A + 12 months + Annual Total + %

function blankProformaRow(style?: Record<string, unknown>): XlsxCell[] {
  return Array.from({ length: PROFORMA_COLS }, () =>
    makeCell('', undefined, style),
  );
}

function buildProformaSheet(
  lineItems: LineItem[],
  hoaName: string,
  year: number,
  reserveInflationRate?: number | null,
): Record<string, unknown> {
  const rows: XlsxCell[][] = [];
  const merges: { s: { r: number; c: number }; e: { r: number; c: number } }[] = [];

  const mergeFull = (rowIdx: number) =>
    merges.push({ s: { r: rowIdx, c: 0 }, e: { r: rowIdx, c: PROFORMA_COLS - 1 } });

  // ── Header block ────────────────────────────────────────────────────────────

  // Row 0: HOA name
  const titleRow = blankProformaRow();
  titleRow[0] = makeCell(hoaName, undefined, STYLES.title);
  rows.push(titleRow);
  mergeFull(0);

  // Row 1: statement title
  const subtitleRow = blankProformaRow();
  subtitleRow[0] = makeCell(
    `OPERATING AND CASH FLOW STATEMENT FOR ${year}`,
    undefined,
    STYLES.subtitle,
  );
  rows.push(subtitleRow);
  mergeFull(1);

  // Row 2: export date
  const exportDate = new Date().toLocaleDateString('en-US', {
    year: 'numeric', month: 'long', day: 'numeric',
  });
  const dateRow = blankProformaRow();
  dateRow[0] = makeCell(`Exported: ${exportDate}`, undefined, STYLES.metaRow);
  rows.push(dateRow);
  mergeFull(2);

  // Row 3: blank spacer
  rows.push(blankProformaRow());

  // ── Column header row ────────────────────────────────────────────────────────

  const colHeaderRow: XlsxCell[] = [
    makeCell('', undefined, STYLES.columnHeader),
    ...MONTHS.map((m) => makeCell(m, undefined, STYLES.columnHeader)),
    makeCell('Annual Total', undefined, STYLES.columnHeader),
    makeCell('% of Budget', undefined, STYLES.columnHeader),
  ];
  rows.push(colHeaderRow);

  // ── Categorized line items ────────────────────────────────────────────────────

  const incomeItems = lineItems.filter((i) => i.category === 'income');
  const reserveContribLine = getReserveContributionLine(lineItems);
  const reserveContribId = reserveContribLine?.id;
  const operatingItems = lineItems.filter(
    (i) => i.category === 'operating' && i.id !== reserveContribId,
  );
  const reserveExpenseItems = lineItems.filter((i) => i.category === 'reserve_expense');

  // Sum proposed values for percentage calculations (computed before rows are written)
  const totalIncomeProposed = incomeItems.reduce(
    (sum, item) => sum + getProposedValue(item, reserveInflationRate),
    0,
  );
  const allOpItems = [...operatingItems, ...reserveExpenseItems];
  const totalOpProposed = allOpItems.reduce(
    (sum, item) => sum + getProposedValue(item, reserveInflationRate),
    0,
  );

  // ── Income section ───────────────────────────────────────────────────────────

  {
    const sectionHeaderRow = blankProformaRow(STYLES.sectionHeaderEmpty);
    sectionHeaderRow[0] = makeCell('INCOME', undefined, STYLES.sectionHeader);
    const sectionRowIdx = rows.length;
    rows.push(sectionHeaderRow);
    merges.push({ s: { r: sectionRowIdx, c: 0 }, e: { r: sectionRowIdx, c: PROFORMA_COLS - 1 } });

    for (const item of incomeItems) {
      const proposed = getProposedValue(item, reserveInflationRate);
      const monthly = proposed / 12;
      const pct = totalIncomeProposed > 0 ? proposed / totalIncomeProposed : 0;
      const isRO = item.readOnly === true;
      const rowStyle = isRO ? STYLES.readOnlyRow : STYLES.dataRow;

      rows.push([
        makeCell(getDisplayName(item), undefined, rowStyle),
        ...MONTHS.map(() => makeCell(monthly, '$#,##0.00', rowStyle)),
        makeCell(proposed, '$#,##0.00', rowStyle),
        makeCell(pct, '0.00%', rowStyle),
      ]);
    }

    // TOTAL OPERATING INCOME
    const totalIncomeMonthly = totalIncomeProposed / 12;
    rows.push([
      makeCell('TOTAL OPERATING INCOME', undefined, STYLES.totalRow),
      ...MONTHS.map(() => makeCell(totalIncomeMonthly, '$#,##0.00', STYLES.totalRow)),
      makeCell(totalIncomeProposed, '$#,##0.00', STYLES.totalRow),
      makeCell(1, '0.00%', STYLES.totalRow),
    ]);

    rows.push(blankProformaRow());
  }

  // ── Operating Expenses section ───────────────────────────────────────────────

  {
    const sectionHeaderRow = blankProformaRow(STYLES.sectionHeaderEmpty);
    sectionHeaderRow[0] = makeCell('OPERATING EXPENSES', undefined, STYLES.sectionHeader);
    const sectionRowIdx = rows.length;
    rows.push(sectionHeaderRow);
    merges.push({ s: { r: sectionRowIdx, c: 0 }, e: { r: sectionRowIdx, c: PROFORMA_COLS - 1 } });

    for (const item of allOpItems) {
      const proposed = getProposedValue(item, reserveInflationRate);
      const monthly = proposed / 12;
      const pct = totalOpProposed > 0 ? proposed / totalOpProposed : 0;
      const isRO = item.readOnly === true;
      const rowStyle = isRO ? STYLES.readOnlyRow : STYLES.dataRow;

      rows.push([
        makeCell(getDisplayName(item), undefined, rowStyle),
        ...MONTHS.map(() => makeCell(monthly, '$#,##0.00', rowStyle)),
        makeCell(proposed, '$#,##0.00', rowStyle),
        makeCell(pct, '0.00%', rowStyle),
      ]);
    }

    // TOTAL OPERATING EXPENSES
    const totalOpMonthly = totalOpProposed / 12;
    rows.push([
      makeCell('TOTAL OPERATING EXPENSES', undefined, STYLES.totalRow),
      ...MONTHS.map(() => makeCell(totalOpMonthly, '$#,##0.00', STYLES.totalRow)),
      makeCell(totalOpProposed, '$#,##0.00', STYLES.totalRow),
      makeCell(1, '0.00%', STYLES.totalRow),
    ]);

    rows.push(blankProformaRow());
  }

  // ── Summary rows ──────────────────────────────────────────────────────────────

  const netAnnual = totalIncomeProposed - totalOpProposed;
  const netMonthly = netAnnual / 12;

  // NET OPERATING INCOME
  rows.push([
    makeCell('NET OPERATING INCOME', undefined, STYLES.netRow),
    ...MONTHS.map(() => makeCell(netMonthly, '$#,##0.00', STYLES.netRow)),
    makeCell(netAnnual, '$#,##0.00', STYLES.netRow),
    makeCell('', undefined, STYLES.netRow),
  ]);

  // MONTHLY CONTRIBUTION TO RESERVE
  const reserveAnnual = reserveContribLine
    ? getProposedValue(reserveContribLine, reserveInflationRate)
    : 0;
  const reserveMonthly = reserveAnnual / 12;
  rows.push([
    makeCell('MONTHLY CONTRIBUTION TO RESERVE', undefined, STYLES.reserveRow),
    ...MONTHS.map(() => makeCell(reserveMonthly, '$#,##0.00', STYLES.reserveRow)),
    makeCell(reserveAnnual, '$#,##0.00', STYLES.reserveRow),
    makeCell('', undefined, STYLES.reserveRow),
  ]);

  // MONTHLY CASH FLOW
  const cashFlowMonthly = netMonthly - reserveMonthly;
  const cashFlowAnnual = cashFlowMonthly * 12;
  rows.push([
    makeCell('MONTHLY CASH FLOW', undefined, STYLES.cashFlowRow),
    ...MONTHS.map(() => makeCell(cashFlowMonthly, '$#,##0.00', STYLES.cashFlowRow)),
    makeCell(cashFlowAnnual, '$#,##0.00', STYLES.cashFlowRow),
    makeCell('', undefined, STYLES.cashFlowRow),
  ]);

  // CUMULATIVE CASH FLOW (running sum Jan–Dec)
  const cumulativeCells: XlsxCell[] = [makeCell('CUMULATIVE CASH FLOW', undefined, STYLES.cumulativeRow)];
  let running = 0;
  for (let m = 0; m < 12; m++) {
    running += cashFlowMonthly;
    cumulativeCells.push(makeCell(running, '$#,##0.00', STYLES.cumulativeRow));
  }
  cumulativeCells.push(makeCell(running, '$#,##0.00', STYLES.cumulativeRow)); // Annual Total = Dec cumulative
  cumulativeCells.push(makeCell('', undefined, STYLES.cumulativeRow));
  rows.push(cumulativeCells);

  // ── Build worksheet ──────────────────────────────────────────────────────────

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const ws: any = XLSXStyle.utils.aoa_to_sheet(rows as any);

  ws['!cols'] = [
    { wch: 40 },
    ...MONTHS.map(() => ({ wch: 12 })),
    { wch: 14 },
    { wch: 10 },
  ];

  ws['!merges'] = merges;

  applyStylesToSheet(ws as Record<string, unknown>, rows);

  return ws as Record<string, unknown>;
}

// ── Detail sheet builder (Sheet 2) ────────────────────────────────────────────

function buildDetailSheet(lineItems: LineItem[]): Record<string, unknown> {
  const rows: XlsxCell[][] = [];
  const merges: { s: { r: number; c: number }; e: { r: number; c: number } }[] = [];

  const border = THIN_BORDER;

  // ── Header ──────────────────────────────────────────────────────────────────

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
  rows.push(headers.map((h) => makeCell(h, undefined, headerStyle)));

  // ── Category sections ────────────────────────────────────────────────────────

  const categories = ['income', 'operating', 'reserve', 'reserve_income', 'reserve_expense'];

  for (const cat of categories) {
    const catItems = lineItems.filter((item) => item.category === cat);
    if (catItems.length === 0) continue;

    // Category header
    const catHeaderRowIdx = rows.length;
    rows.push([
      makeCell(getCategoryLabel(cat), undefined, STYLES.sectionHeader),
      ...Array.from({ length: 7 }, () => makeCell('', undefined, STYLES.sectionHeaderEmpty)),
    ]);
    merges.push({ s: { r: catHeaderRowIdx, c: 0 }, e: { r: catHeaderRowIdx, c: 7 } });

    // Data rows
    for (const item of catItems) {
      const proposed = calcDisplayProposed(item);
      const monthly = calcDisplayMonthly(item);
      const isRO = item.readOnly === true;
      const nameStyle = isRO ? { font: { italic: true }, border } : { border };

      rows.push([
        makeCell(item.accountCode ?? '', undefined, { border }),
        makeCell(item.name, undefined, nameStyle),
        isRO ? makeCell('', undefined, { border }) : makeCell(item.ytdActual, '$#,##0', { border }),
        isRO ? makeCell('', undefined, { border }) : makeCell(item.annualBudget, '$#,##0', { border }),
        isRO ? makeCell('', undefined, { border }) : makeCell((item.percentChange ?? 0) / 100, '0.00%', { border }),
        isRO ? makeCell('', undefined, { border }) : makeCell(proposed, '$#,##0', { border }),
        isRO ? makeCell('', undefined, { border }) : makeCell(monthly, '$#,##0.00', { border }),
        makeCell(item.note?.body ?? '', undefined, { border }),
      ]);
    }

    // Subtotal row
    const subtotalStyle = { font: { bold: true }, border };
    rows.push([
      makeCell('', undefined, subtotalStyle),
      makeCell('Subtotal', undefined, subtotalStyle),
      makeCell(calcCategoryTotal(catItems, 'ytdActual'),      '$#,##0',    subtotalStyle),
      makeCell(calcCategoryTotal(catItems, 'annualBudget'),   '$#,##0',    subtotalStyle),
      makeCell('', undefined, subtotalStyle),
      makeCell(calcCategoryTotal(catItems, 'proposedChange'), '$#,##0',    subtotalStyle),
      makeCell(calcCategoryTotal(catItems, 'monthly'),        '$#,##0.00', subtotalStyle),
      makeCell('', undefined, subtotalStyle),
    ]);

    // Spacer
    rows.push(Array.from({ length: 8 }, () => makeCell('')));
  }

  // ── Grand Total row ──────────────────────────────────────────────────────────

  const opItems  = lineItems.filter((i) => i.category === 'operating');
  const resItems = lineItems.filter(
    (i) => i.category === 'reserve' || i.category === 'reserve_income' || i.category === 'reserve_expense',
  );

  const totalProposed =
    calcCategoryTotal(opItems, 'proposedChange') +
    calcCategoryTotal(resItems, 'proposedChange');
  const totalMonthly =
    calcCategoryTotal(opItems, 'monthly') +
    calcCategoryTotal(resItems, 'monthly');

  rows.push([
    makeCell('', undefined, STYLES.grandTotal),
    makeCell('GRAND TOTAL', undefined, STYLES.grandTotal),
    makeCell('', undefined, STYLES.grandTotal),
    makeCell('', undefined, STYLES.grandTotal),
    makeCell('', undefined, STYLES.grandTotal),
    makeCell(totalProposed, '$#,##0',    STYLES.grandTotal),
    makeCell(totalMonthly,  '$#,##0.00', STYLES.grandTotal),
    makeCell('', undefined, STYLES.grandTotal),
  ]);

  // ── Build worksheet ──────────────────────────────────────────────────────────

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const ws: any = XLSXStyle.utils.aoa_to_sheet(rows as any);

  ws['!cols'] = [
    { wch: 14 }, { wch: 42 }, { wch: 16 }, { wch: 16 },
    { wch: 11 }, { wch: 18 }, { wch: 14 }, { wch: 40 },
  ];

  ws['!merges'] = merges;

  applyStylesToSheet(ws as Record<string, unknown>, rows);

  return ws as Record<string, unknown>;
}

// ── Orchestrator ───────────────────────────────────────────────────────────────

export function exportEnrichedBudget(
  lineItems: LineItem[],
  hoaName: string,
  reserveInflationRate?: number | null,
  year?: number,
): void {
  const fiscalYear = year ?? new Date().getFullYear();

  const proformaSheet = buildProformaSheet(lineItems, hoaName, fiscalYear, reserveInflationRate);
  const detailSheet   = buildDetailSheet(lineItems);

  const wb = XLSXStyle.utils.book_new();
  XLSXStyle.utils.book_append_sheet(wb, proformaSheet, 'Proforma');
  XLSXStyle.utils.book_append_sheet(wb, detailSheet,   'Detail');

  const safeName = hoaName.replace(/[^a-zA-Z0-9 _-]/g, '').trim();
  const filename  = `${safeName || 'Budget'} Budget ${fiscalYear}.xlsx`;

  XLSXStyle.writeFile(wb, filename);
}
