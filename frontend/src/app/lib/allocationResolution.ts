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

export function combinedLineHint(
  issues: Array<{ code: string; details?: Record<string, unknown> }>,
): { lineLabel: string; category: string; poolKey: string } | null {
  const issue = issues.find((item) => item.code === 'combined_line_requires_split');
  if (!issue?.details) return null;
  const lineLabel = String(issue.details.line_label || '').trim();
  if (!lineLabel) return null;
  return {
    lineLabel,
    category: String(issue.details.category || '').trim(),
    poolKey: String(issue.details.pool_key || '').trim(),
  };
}
