export type SliceDraft = {
  pool_key: string;
  semantic_category: string;
  slice_annual_amount: string;
};

export function slicesBalance(sourceAnnual: number, slices: SliceDraft[], tolerance = 0.01): number {
  const total = slices.reduce((sum, slice) => sum + Number(slice.slice_annual_amount || 0), 0);
  const delta = sourceAnnual - total;
  return Math.abs(delta) <= tolerance ? 0 : delta;
}

export function issueAnchor(target: string): string {
  return `allocation-${target.replace(/[^a-zA-Z0-9:_-]/g, '-')}`;
}

export const MISSOURI_CORRECTION = {
  sourceLine: 'Electricity & Gas',
  sourceAnnual: 16800,
  slices: [
    { pool_key: 'variable_dre_exceptions', semantic_category: 'gas', slice_annual_amount: '5600' },
    { pool_key: 'equal_base', semantic_category: 'electricity', slice_annual_amount: '11200' },
  ],
  levyUnit201: 1057.2,
};
