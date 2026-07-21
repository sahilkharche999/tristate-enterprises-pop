/**
 * Display-only reserve inflation formatter (e.g. 0.035 → "3.5%").
 * Do not use for HOAWorkspace form inputs (those omit "%" and parse via /100).
 */
export function formatReserveInflation(rate?: number | null): string {
  const normalizedRate = typeof rate === 'number' && Number.isFinite(rate) ? rate : 0;
  return `${(normalizedRate * 100).toFixed(1)}%`;
}
