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

import { BASE_URL } from './config.ts';
import { authHeaders, handleResponse } from './http.ts';
import type {
  DREApprovalResponse,
  DREDemotionResponse,
  DREDocument,
  DREExtractionRunListItem,
  DREReviewEdit,
  ReopenRepromoteResponse,
} from './dre.ts';

// Re-export DRE types reused by the CC&R panel.
export type {
  DREDocument,
  DREExtractionRunListItem,
  DREApprovalResponse,
  DREDemotionResponse,
  ReopenRepromoteResponse,
};

export interface CCRUnitFactorEntry {
  unit_number: string;
  square_feet?: number | null;
  ownership_percent?: number | null;
  fixed_amounts?: Record<string, number>;
  custom_factors?: Record<string, number>;
}

export interface SaveFactorsResponse {
  extraction_run_id: number;
  factors_saved: number;
}

export type CCRSetupType = 'fixed' | 'grouped' | 'per_unit';

export type CCRPoolCorrectionOperation =
  | {
      operation: 'add';
      base_version: number;
      category_key: string;
      pool: Record<string, unknown>;
    }
  | {
      operation: 'update';
      base_version: number;
      category_key: string;
      changes: Record<string, unknown>;
    }
  | {
      operation: 'split';
      base_version: number;
      category_key: string;
      pools: Array<Record<string, unknown>>;
    }
  | {
      operation: 'merge';
      base_version: number;
      category_keys: string[];
      pool: Record<string, unknown>;
    }
  | {
      operation: 'remove';
      base_version: number;
      category_key: string;
    };

export type CCRRecommendedOperation =
  | Omit<Extract<CCRPoolCorrectionOperation, { operation: 'add' }>, 'base_version'>
  | Omit<Extract<CCRPoolCorrectionOperation, { operation: 'update' }>, 'base_version'>
  | {
      operation: 'set_ownership_percent_form';
      allowed_values: Array<'fraction' | 'points'>;
    }
  | Record<string, unknown>;

export interface CCRPromotionIssue {
  code: string;
  severity: 'warning' | 'error';
  category_key: string | null;
  source_pages: number[];
  explanation: string;
  recommended_operation: CCRRecommendedOperation | null;
  approval_blocked: boolean;
}

export interface CCRPromotionPreview {
  extraction_run_id: number;
  review_version: number;
  resolved_extraction: Record<string, unknown> | null;
  issues: CCRPromotionIssue[];
  approval_blocked: boolean;
}

export interface CCRScalarCorrection {
  field_path: string;
  old_value?: unknown;
  new_value: unknown;
  reason?: string;
}

const DEFAULT_CCR_CORRECTION_ERROR =
  'We could not save that correction. Refresh the review and try again.';

export function parseFriendlyCCRApiError(
  error: unknown,
  fallback = DEFAULT_CCR_CORRECTION_ERROR,
): string {
  const raw =
    error && typeof error === 'object' && 'message' in error
      ? String((error as { message?: unknown }).message || '')
      : '';
  if (/stale|changed while|older version/i.test(raw)) {
    return 'This review changed while you were working. Refresh it and try that choice again.';
  }
  if (/network|failed to fetch|offline/i.test(raw)) {
    return 'We could not reach the server. Check your connection and try again.';
  }
  // Backend details can contain implementation names, validation paths, and
  // machine values. The guided review deliberately exposes only actionable,
  // stable language instead of attempting to partially sanitize those details.
  return fallback;
}

export async function getCCRPromotionPreview(
  hoaId: number,
  runId: number,
  setupType: CCRSetupType,
): Promise<CCRPromotionPreview> {
  const query = new URLSearchParams({ setup_type: setupType });
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/ccr/extraction-runs/${runId}/promotion-preview?${query}`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}

export async function saveCCRCorrectionOperation(
  hoaId: number,
  runId: number,
  operation: CCRPoolCorrectionOperation,
  reason = 'Guided CC&R correction',
): Promise<DREReviewEdit> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/dre/extraction-runs/${runId}/edits`,
    {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        field_path: 'allocation_pools.$operation',
        new_value: operation,
        reason,
      }),
    },
  );
  return handleResponse(res);
}

export async function saveCCRScalarCorrection(
  hoaId: number,
  runId: number,
  correction: CCRScalarCorrection,
): Promise<DREReviewEdit> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/dre/extraction-runs/${runId}/edits`,
    {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...correction,
        reason: correction.reason || 'Guided CC&R correction',
      }),
    },
  );
  return handleResponse(res);
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

// Correct an already-promoted CC&R run without a new extraction/upload.
// Symmetric with reopenAndRepromoteExtractionRun; also re-merges operator
// per-unit factors and re-enforces the missing-unit-factors guard.
export async function reopenAndRepromoteCCRRun(
  hoaId: number,
  runId: number,
  setupType: 'fixed' | 'grouped' | 'per_unit',
): Promise<ReopenRepromoteResponse> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/ccr/extraction-runs/${runId}/repromote`,
    {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ setup_type: setupType }),
    },
  );
  return handleResponse(res);
}
