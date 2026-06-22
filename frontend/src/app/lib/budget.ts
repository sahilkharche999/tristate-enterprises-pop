/**
 * Shared budget calculation utilities.
 * All formulas mirror the backend pipeline (generate_budget_pipeline.py).
 */
import { type LineItem } from '../data/mockData.ts';

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
    case 'income':          return 'INCOME';
    case 'operating':       return 'OPERATING EXPENSES';
    case 'reserve':         return 'RESERVE CONTRIBUTIONS';
    case 'reserve_income':  return 'RESERVE INCOME';
    case 'reserve_expense': return 'RESERVE EXPENSES';
    default:                return category.toUpperCase().replace('_', ' ');
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

export const normalizeReserveInflationRate = (rate?: number | null): number =>
  typeof rate === 'number' && Number.isFinite(rate) ? Math.max(rate, 0) : 0;

export const isReserveCategory = (category: string): boolean =>
  category === 'reserve' || category === 'reserve_income' || category === 'reserve_expense';

export const isReserveComponent = (item: LineItem): boolean => {
  if (!isReserveCategory(item.category) || !item.readOnly) {
    return false;
  }
  if (item.reserveGroup) {
    return item.reserveGroup === 'component';
  }
  // reserve_expense items that are read-only are reserve study components
  if (item.category === 'reserve_expense') {
    return true;
  }
  // Legacy fallback for old data with category='reserve'
  const section = (item.rawSection || '').trim().toLowerCase();
  if (section === 'reserve expense' || section === 'reserve expenses (per reserve study)') {
    return true;
  }
  return false;
};

export const calcReserveAdjustedAmount = (item: LineItem, reserveInflationRate?: number | null): number => {
  const normalizedRate = normalizeReserveInflationRate(reserveInflationRate);
  if (!isReserveComponent(item) || item.annualBudget <= 0) {
    return item.annualBudget;
  }
  return item.annualBudget * (1 + normalizedRate);
};

export const calcDisplayProposed = (item: LineItem, reserveInflationRate?: number | null): number => {
  if (isReserveComponent(item)) {
    return calcReserveAdjustedAmount(item, reserveInflationRate);
  }
  if (item.readOnly) {
    return 0;
  }
  return calcProposed(item.annualBudget, item.percentChange);
};

export const calcDisplayMonthly = (item: LineItem, reserveInflationRate?: number | null): number =>
  calcMonthly(calcDisplayProposed(item, reserveInflationRate));

export const calcSettingsDerivedReservePercent = (
  item: LineItem,
  reserveInflationRate?: number | null,
): number => {
  if (!isReserveComponent(item) || item.annualBudget <= 0) {
    return 0;
  }
  return normalizeReserveInflationRate(reserveInflationRate) * 100;
};

// ── Category aggregation ──────────────────────────────────────────────────────

export type TotalField = 'ytdActual' | 'annualBudget' | 'projection' | 'proposedChange' | 'monthly';

/** Proposed income total — sum over all income-category items. */
export const calcTotalIncome = (
  incomeItems: LineItem[],
  reserveInflationRate?: number | null,
): number =>
  calcDisplayCategoryTotal(incomeItems, 'proposedChange', reserveInflationRate);

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

// MARKER_FRONTEND_BUILD_PROOF_20260407_2014_INCLUDEREADONLY_FIX_LIVE
export const calcDisplayCategoryTotal = (
  items: LineItem[],
  field: TotalField,
  reserveInflationRate?: number | null,
  includeReadOnly: boolean = false,
): number => {
  if (typeof window !== 'undefined') {
    (window as unknown as { __CALC_DISPLAY_CATEGORY_TOTAL_MARKER__?: string }).__CALC_DISPLAY_CATEGORY_TOTAL_MARKER__ = 'INCLUDEREADONLY_FIX_LIVE_20260407';
  }
  return items.reduce((sum, item) => {
    // Two modes:
    //   includeReadOnly=false (default): used for cross-category grand totals.
    //     Reserve items are excluded so they don't double-count with the
    //     operating "Allocation to Reserves" line that funds them.
    //   includeReadOnly=true: used for per-category subtotal rows. The subtotal
    //     for a read-only category (reserve_income, reserve_expense) must sum
    //     the real values — otherwise it displays $0 even though the rows show
    //     real numbers, which misleads the user.
    // Note: calcDisplayProposed/calcDisplayMonthly still return 0 for read-only
    // items, so per-category subtotals for 'proposedChange' and 'monthly' will
    // correctly show $0 (read-only items have no proposed adjustments).
    if (item.readOnly && !includeReadOnly) return sum;
    if (field === 'projection') {
      return sum + (item.projection ?? 0);
    }
    if (field === 'proposedChange') {
      return sum + calcDisplayProposed(item, reserveInflationRate);
    }
    if (field === 'monthly') {
      return sum + calcDisplayMonthly(item, reserveInflationRate);
    }
    if (field === 'annualBudget') {
      return sum + item.annualBudget;
    }
    return sum + item.ytdActual;
  }, 0);
};
