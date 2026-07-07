const MIN_ZOOM_PERCENT = 50;
const MAX_ZOOM_PERCENT = 150;
export const TABLE_ZOOM_STEP = 10;

// Keeps the reserve-study table's zoom control from shrinking it to
// illegible or blowing it up past the point zoom-out was meant to solve.
export function clampTableZoomPercent(percent: number): number {
  return Math.min(MAX_ZOOM_PERCENT, Math.max(MIN_ZOOM_PERCENT, percent));
}
