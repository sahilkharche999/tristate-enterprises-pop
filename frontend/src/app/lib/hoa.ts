import type { HOARecord } from '../api/hoa';

export const MONTH_NAMES = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
] as const;

export interface HOAViewModel {
  id: string;
  hoaCode: string;
  name: string;
  fiscalYear: string;
  status: string;
  units: number;
  taxId: string;
  fiscalYearStart: string;
  year: number;
  city: string;
}

export function monthNumberToName(month: number): string {
  return MONTH_NAMES[month - 1] ?? MONTH_NAMES[0];
}

export function monthNameToNumber(name: string): number {
  const normalized = name.trim().toLowerCase();
  const index = MONTH_NAMES.findIndex((month) => month.toLowerCase() === normalized);
  return index >= 0 ? index + 1 : 1;
}

export function formatFiscalYearLabel(startMonth: number, endMonth: number): string {
  return `${monthNumberToName(startMonth)}-${monthNumberToName(endMonth)}`;
}

function formatTwoDigitYear(year: number): string {
  return String(year).slice(-2);
}

export function formatFiscalYearRangeLabel(
  startMonth: number,
  endMonth: number,
  startYear?: number | null,
): string {
  const resolvedStartYear = startYear ?? new Date().getFullYear();
  const resolvedEndYear = endMonth < startMonth ? resolvedStartYear + 1 : resolvedStartYear;
  return `${monthNumberToName(startMonth)}/${formatTwoDigitYear(resolvedStartYear)} - ${monthNumberToName(endMonth)}/${formatTwoDigitYear(resolvedEndYear)}`;
}

export function toHOAViewModel(hoa: HOARecord): HOAViewModel {
  return {
    id: String(hoa.id),
    hoaCode: hoa.hoa_code,
    name: hoa.name,
    fiscalYear: formatFiscalYearLabel(hoa.fiscal_year_start_month, hoa.fiscal_year_end_month),
    status: hoa.workflow_status || 'Not Started',
    units: hoa.units ?? 0,
    taxId: hoa.tax_id || '',
    fiscalYearStart: monthNumberToName(hoa.fiscal_year_start_month),
    year: hoa.portfolio_year ?? new Date().getFullYear(),
    city: hoa.city || 'Unknown',
  };
}
