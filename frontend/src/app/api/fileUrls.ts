import { BASE_URL } from './config.ts';

// Pure URL builders (no fat client deps) so node:test can import them in isolation.
// Path templates must stay byte-identical to the previous per-module helpers.

export function dreDocumentFileUrl(hoaId: number, documentId: number): string {
  return `${BASE_URL}/hoa/${hoaId}/dre/documents/${documentId}/file`;
}

export function reserveStudyFileUrl(hoaId: number | string, uploadId: number): string {
  return `${BASE_URL}/hoa/${hoaId}/budget/uploads/${uploadId}/file`;
}

export function incomeStatementFileUrl(hoaId: number | string, uploadId: number): string {
  return `${BASE_URL}/hoa/${hoaId}/budget/uploads/${uploadId}/income-statement-file`;
}

export function incomeStatementHtmlFileUrl(hoaId: number | string, uploadId: number): string {
  return `${BASE_URL}/hoa/${hoaId}/budget/uploads/${uploadId}/income-statement-file-html`;
}
