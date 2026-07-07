// Standard "open at page N" URL fragment, honored on initial load by
// Chromium/Firefox's built-in PDF viewer. See design.md Decision 1/1a in
// openspec/changes/add-dre-review-pdf-compare-view — this only works
// reliably on a fresh load, which is why DrePdfCompareView also remounts
// the iframe (key={targetPage}) on every jump rather than just updating src.
export function pdfPageAnchorUrl(url: string, page: number | undefined): string {
  if (!url || page == null) return url;
  return `${url}#page=${page}`;
}
