// Income-statement citations are heterogeneous by source: "page N" for
// PDF extractions, "Sheet1!B12" for Excel, or null. This only ever recognizes
// the PDF "page N" shape — it must never guess a page number out of an Excel
// cell reference (see design.md Decision 3).
const PAGE_PATTERN = /^page\s+(\d+)$/i;

export function parseSourcePage(value: string | null | undefined): number | null {
  if (!value) return null;
  const match = PAGE_PATTERN.exec(value.trim());
  if (!match) return null;
  return Number.parseInt(match[1], 10);
}
