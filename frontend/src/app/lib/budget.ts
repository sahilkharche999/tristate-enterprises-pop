/**
 * Shared budget calculation utilities.
 * All formulas mirror the backend pipeline (generate_budget_pipeline.py).
 */
import { type LineItem } from '../data/mockData';

// ── Formatting ────────────────────────────────────────────────────────────────

const currencyFmt0 = new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 0,
});
const currencyFmt2 = new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
});

export const formatCurrency = (value: number, decimals: 0 | 2 = 0) =>
  (decimals === 2 ? currencyFmt2 : currencyFmt0).format(value);

export const formatTimestamp = (date: Date) =>
  date.toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });

// ── Category labels ───────────────────────────────────────────────────────────

export const getCategoryLabel = (category: string): string => {
  switch (category) {
    case 'income':    return 'INCOME';
    case 'operating': return 'OPERATING EXPENSES';
    case 'reserve':   return 'RESERVE CONTRIBUTIONS';
    default:          return category.toUpperCase();
  }
};

// ── Core formulas (match backend col formulas exactly) ────────────────────────

/** Backend col AN: proposed = annualBudget × (1 + % change) */
export const calcProposed = (annualBudget: number, percentChange: number): number =>
  annualBudget * (1 + percentChange / 100);

/** Backend col AO–AR: monthly = proposed / 12 */
export const calcMonthly = (proposed: number): number => proposed / 12;

/** Backend col AK: % diff = (projection − annualBudget) / annualBudget */
export const calcPercentDiff = (projection: number, annualBudget: number): number => {
  if (annualBudget === 0) return 0;
  return ((projection - annualBudget) / annualBudget) * 100;
};

// ── Category aggregation ──────────────────────────────────────────────────────

export type TotalField = 'ytdActual' | 'annualBudget' | 'projection' | 'proposedChange' | 'monthly';

export const calcCategoryTotal = (items: LineItem[], field: TotalField): number =>
  items.reduce((sum, item) => {
    // readOnly rows are excluded from the board-adjustable budget flow — never count in totals
    if (item.readOnly) return sum;
    if (field === 'projection')     return sum + (item.projection ?? 0);
    if (field === 'proposedChange') return sum + calcProposed(item.annualBudget, item.percentChange);
    if (field === 'monthly')        return sum + calcMonthly(calcProposed(item.annualBudget, item.percentChange));
    if (field === 'annualBudget')   return sum + item.annualBudget;
    return sum + item.ytdActual;
  }, 0);
