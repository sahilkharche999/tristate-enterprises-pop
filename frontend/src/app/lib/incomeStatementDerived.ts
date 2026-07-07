import type { LineItem } from '../data/mockData';
import { parseSourcePage } from './incomeStatementSourcePage.ts';

// Kept separate from reserveStudyDerived.ts's earliestReserveStudyPage — one
// reads a typed source_page: Optional[int], the other parses a heterogeneous
// citation string; no shared abstraction is introduced only to save a file.
export function earliestSourcePage(rows: LineItem[]): number | undefined {
  let earliest: number | undefined;
  for (const row of rows) {
    const page = parseSourcePage(row.sourcePageOrCell);
    if (page === null) continue;
    if (earliest === undefined || page < earliest) {
      earliest = page;
    }
  }
  return earliest;
}
