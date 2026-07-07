import { BASE_URL } from './config.ts';

// Kept in its own module (rather than inline in dre.ts) so it stays a pure,
// dependency-light function that node:test can import directly without
// pulling in dre.ts's other extensionless internal imports.
export function dreDocumentFileUrl(hoaId: number, documentId: number): string {
  return `${BASE_URL}/hoa/${hoaId}/dre/documents/${documentId}/file`;
}
