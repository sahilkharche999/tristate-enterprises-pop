// CC&R / governing-document API client.
//
// Endpoints (backend routers/ccr.py):
//   POST   /hoa/{id}/ccr/upload                                 → upload PDF
//   POST   /hoa/{id}/ccr/documents/{doc_id}/extract             → schedule extraction
//   GET    /hoa/{id}/ccr/documents                              → DREDocumentResponse[]
//   GET    /hoa/{id}/ccr/extraction-runs                        → DREExtractionRunListItem[]
//   POST   /hoa/{id}/ccr/extraction-runs/{run_id}/factors       → save per-unit factors
//   GET    /hoa/{id}/ccr/extraction-runs/{run_id}/factors       → current factors
//   POST   /hoa/{id}/ccr/extraction-runs/{run_id}/approve       → promote to AssessmentSetup

import { BASE_URL } from './config';
import { authHeaders, handleResponse } from './http';
import type {
  DREApprovalResponse,
  DREDemotionResponse,
  DREDocument,
  DREExtractionRunListItem,
} from './dre';

// Re-export DRE types reused by the CC&R panel.
export type {
  DREDocument,
  DREExtractionRunListItem,
  DREApprovalResponse,
  DREDemotionResponse,
};

export interface CCRUnitFactorEntry {
  unit_number: string;
  square_feet?: number | null;
  ownership_percent?: number | null;
}

export interface SaveFactorsResponse {
  extraction_run_id: number;
  factors_saved: number;
}

export async function uploadCCR(
  hoaId: number,
  file: File,
): Promise<{ dre_document_id: number; file_name: string; page_count: number | null }> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/ccr/upload`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  });
  return handleResponse(res);
}

export async function triggerCCRExtraction(
  hoaId: number,
  documentId: number,
): Promise<{ extraction_run_id: number; job_status: string; status: string }> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/ccr/documents/${documentId}/extract`,
    { method: 'POST', headers: authHeaders() },
  );
  return handleResponse(res);
}

export async function listCCRDocuments(hoaId: number): Promise<DREDocument[]> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/ccr/documents`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function listCCRExtractionRuns(
  hoaId: number,
): Promise<DREExtractionRunListItem[]> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/ccr/extraction-runs`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function saveCCRUnitFactors(
  hoaId: number,
  runId: number,
  factors: CCRUnitFactorEntry[],
): Promise<SaveFactorsResponse> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/ccr/extraction-runs/${runId}/factors`,
    {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ factors }),
    },
  );
  return handleResponse(res);
}

export async function getCCRUnitFactors(
  hoaId: number,
  runId: number,
): Promise<Record<string, { square_feet?: number; ownership_percent?: number }>> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/ccr/extraction-runs/${runId}/factors`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}

export async function approveCCRRun(
  hoaId: number,
  runId: number,
  setupType: 'fixed' | 'grouped' | 'per_unit',
): Promise<DREApprovalResponse> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/ccr/extraction-runs/${runId}/approve`,
    {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ setup_type: setupType }),
    },
  );
  return handleResponse(res);
}

// Reverse a CC&R promotion (symmetric with demoteExtractionRun for DRE).
export async function demoteCCRRun(
  hoaId: number,
  runId: number,
): Promise<DREDemotionResponse> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/ccr/extraction-runs/${runId}/demote`,
    { method: 'POST', headers: authHeaders() },
  );
  return handleResponse(res);
}
