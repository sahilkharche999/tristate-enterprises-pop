import { BASE_URL } from './config.ts';

// Kept in its own module (rather than inline in budgetHistory.ts) so it stays a
// pure, dependency-light function that node:test can import directly without
// pulling in budgetHistory.ts's other extensionless internal imports.
export function incomeStatementFileUrl(hoaId: number | string, uploadId: number): string {
  return `${BASE_URL}/hoa/${hoaId}/budget/uploads/${uploadId}/income-statement-file`;
}

export function incomeStatementHtmlFileUrl(hoaId: number | string, uploadId: number): string {
  return `${BASE_URL}/hoa/${hoaId}/budget/uploads/${uploadId}/income-statement-file-html`;
}
