import type { HOA } from '../data/mockData';

const MONTH_NAMES: Record<string, number> = {
  january: 1, february: 2, march: 3, april: 4,
  may: 5, june: 6, july: 7, august: 8,
  september: 9, october: 10, november: 11, december: 12,
};

export function parseMonth(name: string): number {
  const result = MONTH_NAMES[name.toLowerCase()];
  if (result === undefined) {
    console.warn(`[fiscalYear] Unrecognised month name "${name}", defaulting to January`);
    return 1;
  }
  return result;
}

export interface TimingInputs {
  pctYearElapsed: number; // 0.0–1.0: fraction of fiscal year elapsed
  statementMonth: number; // 1–12: current calendar month
  growthFactor: number;   // always 1.0 — no time-series data available
}

/**
 * Derive timing inputs for the AI suggestion API from the HOA's fiscal year
 * settings and today's date.
 *
 * Example: HOA with fiscalYearStart="April", today=October →
 *   months elapsed = 6 → pctYearElapsed = 0.50, statementMonth = 10
 */
export function computeTimingInputs(hoa: HOA, overrideMonth?: number): TimingInputs {
  const today = new Date();
  const currentMonth = overrideMonth ?? (today.getMonth() + 1); // 1-based

  const fiscalStartMonth = parseMonth(hoa.fiscalYearStart);
  const monthsElapsed =
    currentMonth >= fiscalStartMonth
      ? currentMonth - fiscalStartMonth
      : 12 - fiscalStartMonth + currentMonth;

  // monthsElapsed is 0-indexed: 0 = still in the first month of the fiscal year.
  // The AI backend uses pctYearElapsed=0.000 to mean "no complete months yet",
  // which is correct for day 1 of the fiscal year.
  const pctYearElapsed = parseFloat((monthsElapsed / 12).toFixed(3));

  return {
    pctYearElapsed,
    statementMonth: currentMonth,
    growthFactor: 1.0,
  };
}
