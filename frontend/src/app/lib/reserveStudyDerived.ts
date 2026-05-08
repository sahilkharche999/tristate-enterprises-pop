import type { ReserveStudyRow } from '../api/budgetHistory';

function roundHalfEvenWholeDollar(value: number): number {
  const floor = Math.floor(value);
  const fraction = value - floor;
  const epsilon = 1e-9;

  if (fraction < 0.5 - epsilon) return floor;
  if (fraction > 0.5 + epsilon) return Math.ceil(value);
  return floor % 2 === 0 ? floor : floor + 1;
}

export function deriveRemainingLife(row: ReserveStudyRow): number | null {
  if (typeof row.remaining_life === 'number') {
    return Math.max(row.remaining_life, 0);
  }
  if (
    typeof row.useful_life !== 'number' ||
    typeof row.year_new !== 'number' ||
    typeof row.reference_year !== 'number'
  ) {
    return null;
  }
  return Math.max(row.useful_life - (row.reference_year - row.year_new), 0);
}

export function getDisplayYearReplacementProvision(row: ReserveStudyRow): number | null {
  if (typeof row.year_replacement_provision === 'number') {
    return roundHalfEvenWholeDollar(row.year_replacement_provision);
  }
  if (typeof row.replacement_cost !== 'number' || typeof row.useful_life !== 'number' || row.useful_life === 0) {
    return null;
  }
  return roundHalfEvenWholeDollar(row.replacement_cost / row.useful_life);
}

export function getDisplayEstimatedLiability(row: ReserveStudyRow): number | null {
  if (typeof row.estimated_liability === 'number') {
    return roundHalfEvenWholeDollar(row.estimated_liability);
  }
  const remainingLife = deriveRemainingLife(row);
  if (
    typeof row.replacement_cost !== 'number' ||
    typeof row.useful_life !== 'number' ||
    row.useful_life === 0 ||
    remainingLife === null
  ) {
    return null;
  }
  return roundHalfEvenWholeDollar(
    row.replacement_cost * ((row.useful_life - remainingLife) / row.useful_life),
  );
}
