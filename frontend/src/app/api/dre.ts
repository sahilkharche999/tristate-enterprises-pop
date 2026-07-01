// DRE upload + Review Workbench API client (Phase 3+4 of dre-driven-assessment-engine).
//
// Endpoints (backend):
//   POST   /hoa/{hoa_id}/dre/upload                                          → upload PDF
//   POST   /hoa/{hoa_id}/dre/documents/{document_id}/extract                 → schedule Gemini extraction
//   GET    /hoa/{hoa_id}/dre/documents                                       → DREDocument[]
//   GET    /hoa/{hoa_id}/dre/extraction-runs                                 → DREExtractionRun[]
//   GET    /hoa/{hoa_id}/dre/extraction-runs/{run_id}                        → detail
//   GET    /hoa/{hoa_id}/dre/extraction-runs/{run_id}/edits                  → edit history
//   GET    /hoa/{hoa_id}/dre/extraction-runs/{run_id}/field-sources          → source citations
//   POST   /hoa/{hoa_id}/dre/extraction-runs/{run_id}/edits                  → record edit
//   POST   /hoa/{hoa_id}/dre/extraction-runs/{run_id}/approve                → promote to AssessmentSetup

import { BASE_URL } from './config';
import { authHeaders, handleResponse } from './http';

export interface DREDocument {
  document_id: number;
  property_id: number;
  file_id: string;
  file_name: string;
  page_count: number | null;
  status: string;
  uploaded_by: string | null;
  uploaded_at: string;
  supersedes_id: number | null;
}

export interface DREExtractionRunListItem {
  extraction_run_id: number;
  dre_document_id: number;
  property_id: number;
  started_at: string;
  job_status: string;
  status: string;
  review_status: string;
  promoted_setup_id: number | null;
  promoted_at: string | null;
  completed_at: string | null;
  model_name: string | null;
  prompt_version: string | null;
  repair_attempt_count: number;
  error_message: string;
  document_type: string;
}

export interface DREExtractionRunDetail extends DREExtractionRunListItem {
  prompt_sha256: string | null;
  parsed_json: Record<string, unknown> | null;
  citation_audit: unknown[] | null;
  low_confidence_flags: unknown[] | null;
  validation_warnings: unknown[] | null;
  schema_validation_errors: unknown[] | null;
}

export interface DREReviewEdit {
  edit_id: number;
  dre_extraction_run_id: number;
  field_path: string;
  old_value: string | null;
  new_value: string | null;
  reason: string | null;
  edited_by: string | null;
  edited_at: string;
}

export interface ExtractedFieldSource {
  source_id: number;
  dre_extraction_run_id: number;
  entity_type: string;
  entity_id: string | null;
  field_name: string;
  page_number: number;
  bounding_box_json: string | null;
  confidence: number | null;
}

export interface DREApprovalResponse {
  extraction_run_id: number;
  promoted_setup_id: number;
  setup_type: 'fixed' | 'grouped' | 'per_unit';
  promoted_at: string;
  reviewed_by: string | null;
  snapshot_counts: Record<string, number>;
}

export interface ReopenRepromoteResponse {
  extraction_run_id: number;
  promoted_setup_id: number;
  superseded_setup_id: number | null;
  setup_type: 'fixed' | 'grouped' | 'per_unit';
  promoted_at: string;
  reviewed_by: string | null;
  snapshot_counts: Record<string, number>;
  affected_draft_package_ids: number[];
}

export interface DREDemotionResponse {
  extraction_run_id: number;
  demoted_setup_id: number;
  restored_setup_id: number | null;
  review_status: string;
  default_assessment_setup_id: number | null;
}

export async function uploadDRE(
  hoaId: number,
  file: File,
): Promise<{ document_id: number; file_name: string; page_count: number | null }> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/dre/upload`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  });
  return handleResponse(res);
}

export async function triggerDREExtraction(
  hoaId: number,
  documentId: number,
): Promise<{
  document_id: number;
  extraction_run_id: number;
  job_status: string;
  status: string;
}> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/dre/documents/${documentId}/extract`,
    { method: 'POST', headers: authHeaders() },
  );
  return handleResponse(res);
}

export async function listDREDocuments(hoaId: number): Promise<DREDocument[]> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/dre/documents`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function listExtractionRuns(
  hoaId: number,
): Promise<DREExtractionRunListItem[]> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/dre/extraction-runs`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function getExtractionRun(
  hoaId: number,
  runId: number,
): Promise<DREExtractionRunDetail> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/dre/extraction-runs/${runId}`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}

export async function listReviewEdits(
  hoaId: number,
  runId: number,
): Promise<DREReviewEdit[]> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/dre/extraction-runs/${runId}/edits`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}

export async function listFieldSources(
  hoaId: number,
  runId: number,
  filter: { entity_type?: string; entity_id?: string } = {},
): Promise<ExtractedFieldSource[]> {
  const qs = new URLSearchParams();
  if (filter.entity_type) qs.set('entity_type', filter.entity_type);
  if (filter.entity_id) qs.set('entity_id', filter.entity_id);
  const url =
    `${BASE_URL}/hoa/${hoaId}/dre/extraction-runs/${runId}/field-sources` +
    (qs.toString() ? `?${qs.toString()}` : '');
  const res = await fetch(url, { headers: authHeaders() });
  return handleResponse(res);
}

export async function recordReviewEdit(
  hoaId: number,
  runId: number,
  body: {
    field_path: string;
    old_value?: unknown;
    new_value?: unknown;
    reason?: string;
  },
): Promise<DREReviewEdit> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/dre/extraction-runs/${runId}/edits`,
    {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  return handleResponse(res);
}

export async function approveExtractionRun(
  hoaId: number,
  runId: number,
  setupType: 'fixed' | 'grouped' | 'per_unit',
): Promise<DREApprovalResponse> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/dre/extraction-runs/${runId}/approve`,
    {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ setup_type: setupType }),
    },
  );
  return handleResponse(res);
}

// Reverse a promotion: unseat a wrongly-promoted DRE run. Supersedes the
// promoted AssessmentSetup and restores the prior one (or clears the
// property default so a different document can be promoted instead).
export async function demoteExtractionRun(
  hoaId: number,
  runId: number,
): Promise<DREDemotionResponse> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/dre/extraction-runs/${runId}/demote`,
    { method: 'POST', headers: authHeaders() },
  );
  return handleResponse(res);
}

// Correct an already-promoted run without a new extraction/upload: re-applies
// the original extraction plus whatever review edits exist now, supersedes
// the current AssessmentSetup, and promotes a fresh one.
export async function reopenAndRepromoteExtractionRun(
  hoaId: number,
  runId: number,
  setupType: 'fixed' | 'grouped' | 'per_unit',
): Promise<ReopenRepromoteResponse> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/dre/extraction-runs/${runId}/repromote`,
    {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ setup_type: setupType }),
    },
  );
  return handleResponse(res);
}
