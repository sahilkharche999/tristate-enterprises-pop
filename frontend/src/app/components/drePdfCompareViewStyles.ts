export type ComparePdfPaneStatus = 'loading' | 'error' | 'ready';

// The PDF pane shows a plain status message while the authenticated blob
// fetch is in flight or failed; once ready, the iframe itself is the content
// and no overlay message is shown. sourceLabel defaults to 'PDF' so existing
// call sites (Reserve Study, DRE) keep today's exact strings unchanged.
export function comparePdfPaneMessage(
  status: ComparePdfPaneStatus,
  sourceLabel: string = 'PDF',
): string | null {
  if (status === 'loading') return `Loading source ${sourceLabel}…`;
  if (status === 'error') return `Could not load the source ${sourceLabel}.`;
  return null;
}
