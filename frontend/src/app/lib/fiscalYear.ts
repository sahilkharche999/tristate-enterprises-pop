import type { HOARecord } from '../api/hoa';

export interface TimingInputs {
  pctYearElapsed: number; // 0.0–1.0: fraction of fiscal year elapsed
  statementMonth: number; // 1–12: current calendar month
  growthFactor: number;   // always 1.0 — no time-series data available
}

/**
 * Derive timing inputs for the AI suggestion API from the HOA's fiscal year
 * settings and today's date.
 *
 * Example: HOA with fiscal_year_start_month=4 (April), today=October →
 *   months elapsed = 6 → pctYearElapsed = 0.50, statementMonth = 10
 */
export function computeTimingInputs(hoa: HOARecord, overrideMonth?: number): TimingInputs {
  const today = new Date();
  const currentMonth = overrideMonth ?? (today.getMonth() + 1);

  const fiscalStartMonth = hoa.fiscal_year_start_month || 1;
  const monthsElapsed =
    currentMonth >= fiscalStartMonth
      ? currentMonth - fiscalStartMonth
      : 12 - fiscalStartMonth + currentMonth;

  const pctYearElapsed = parseFloat((monthsElapsed / 12).toFixed(3));

  return {
    pctYearElapsed,
    statementMonth: currentMonth,
    growthFactor: 1.0,
  };
}
