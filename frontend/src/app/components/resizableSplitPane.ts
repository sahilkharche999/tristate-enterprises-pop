const MIN_SPLIT_PERCENT = 20;
const MAX_SPLIT_PERCENT = 80;

// Keeps the draggable divider in DrePdfCompareView from collapsing either
// pane down to nothing.
export function clampSplitPercent(percent: number): number {
  return Math.min(MAX_SPLIT_PERCENT, Math.max(MIN_SPLIT_PERCENT, percent));
}
