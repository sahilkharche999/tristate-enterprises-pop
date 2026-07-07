export type ComparePdfPaneStatus = 'loading' | 'error' | 'ready';

// The PDF pane shows a plain status message while the authenticated blob
// fetch is in flight or failed; once ready, the iframe itself is the content
// and no overlay message is shown.
export function comparePdfPaneMessage(status: ComparePdfPaneStatus): string | null {
  if (status === 'loading') return 'Loading source PDF…';
  if (status === 'error') return 'Could not load the source PDF.';
  return null;
}
