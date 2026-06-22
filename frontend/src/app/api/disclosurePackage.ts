// Typed API client for the disclosure-package compiler endpoints (Plan 11-06).
//
// Endpoints (backend at backend/app/disclosure_package/router.py):
//   POST /api/disclosure-package/generate           → 202 { id, status, fiscal_year, property_id }
//   GET  /api/disclosure-package/{job_id}/status    → 200 DisclosurePackageJob
//   GET  /api/disclosure-package/{job_id}/download  → 200 application/pdf
//   GET  /api/disclosure-package/{job_id}/audit     → 200 application/json
//
// All endpoints require auth. Cross-user reads return 404 (T-11-01) so the
// generic 404 handling in handleResponse is sufficient.

import { BASE_URL } from './config';
import { authHeaders, handleBlobResponse, handleResponse } from './http';

export type DisclosurePackageJobStatus = 'pending' | 'running' | 'completed' | 'failed';

export type DisclosurePackageStage =
  | 'validating'
  | 'computing'
  | 'rendering'
  | 'merging'
  | 'verifying'
  | null;

export interface DisclosurePackageJob {
  id: string;
  property_id: number;
  fiscal_year: number;
  status: DisclosurePackageJobStatus;
  stage?: DisclosurePackageStage;
  error_message?: string | null;
  output_path?: string | null;
  audit_path?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
}

export interface AuditLogEntry {
  formula_id: string;
  version: number;
  inputs: Record<string, unknown>;
  output: unknown;
  computed_at: string;
}

export interface AuditLogResponse {
  input_snapshot: Record<string, unknown>;
  formula_calls: AuditLogEntry[];
  started_at: string;
  completed_at?: string | null;
}

export async function generateDisclosurePackage(
  hoaId: number,
  fiscalYear: number,
): Promise<DisclosurePackageJob> {
  const res = await fetch(`${BASE_URL}/api/disclosure-package/generate`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify({ hoa_id: hoaId, fiscal_year: fiscalYear }),
  });
  return handleResponse<DisclosurePackageJob>(res);
}

export async function getDisclosurePackageStatus(
  jobId: string,
): Promise<DisclosurePackageJob> {
  const res = await fetch(
    `${BASE_URL}/api/disclosure-package/${encodeURIComponent(jobId)}/status`,
    {
      headers: authHeaders(),
    },
  );
  return handleResponse<DisclosurePackageJob>(res);
}

export async function getDisclosurePackageAudit(
  jobId: string,
): Promise<AuditLogResponse> {
  const res = await fetch(
    `${BASE_URL}/api/disclosure-package/${encodeURIComponent(jobId)}/audit`,
    {
      headers: authHeaders(),
    },
  );
  return handleResponse<AuditLogResponse>(res);
}

export function disclosurePackageDownloadUrl(jobId: string): string {
  return `${BASE_URL}/api/disclosure-package/${encodeURIComponent(jobId)}/download`;
}

/** Parse the filename from a Content-Disposition header, preferring the
 *  RFC 5987 `filename*=utf-8''...` form, then the quoted `filename="..."`. */
function parseContentDispositionFilename(header: string | null): string | null {
  if (!header) return null;
  const star = header.match(/filename\*=(?:UTF-8'')?([^;]+)/i);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ''));
    } catch {
      /* fall through to plain filename */
    }
  }
  const plain = header.match(/filename="?([^";]+)"?/i);
  return plain?.[1]?.trim() ?? null;
}

export interface DownloadedPdf {
  blob: Blob;
  /** Server-provided filename from Content-Disposition, when present. */
  filename: string | null;
}

export async function downloadDisclosurePackagePdf(jobId: string): Promise<DownloadedPdf> {
  const res = await fetch(disclosurePackageDownloadUrl(jobId), {
    headers: authHeaders(),
  });
  const filename = parseContentDispositionFilename(
    res.headers.get('Content-Disposition'),
  );
  const blob = await handleBlobResponse(res);
  return { blob, filename };
}

// ── Preflight readiness ───────────────────────────────────────────────────────

export type PreflightSeverity = 'blocking' | 'warning';

export interface PreflightFinding {
  field_path: string;
  message: string;
  severity: PreflightSeverity;
  code?: string | null;
  suggested_fix?: string | null;
}

export interface DisclosurePreflightResult {
  ready: boolean;
  blocking: PreflightFinding[];
  warnings: PreflightFinding[];
}

export async function getDisclosurePreflight(
  hoaId: number,
  fiscalYear: number,
): Promise<DisclosurePreflightResult> {
  const params = new URLSearchParams({ fiscal_year: String(fiscalYear) });
  const res = await fetch(
    `${BASE_URL}/api/disclosure-package/hoa/${hoaId}/preflight?${params}`,
    { headers: authHeaders() },
  );
  return handleResponse<DisclosurePreflightResult>(res);
}

// ── Per-HOA static-appendix uploads ──────────────────────────────────────────

export interface DisclosureAppendixEntry {
  filename: string;
  size_bytes: number;
  uploaded_at: string;
}

export async function listDisclosureAppendices(
  hoaId: number,
): Promise<DisclosureAppendixEntry[]> {
  const res = await fetch(
    `${BASE_URL}/api/disclosure-package/hoa/${hoaId}/appendices`,
    { headers: authHeaders() },
  );
  const body = await handleResponse<{ items: DisclosureAppendixEntry[] }>(res);
  return body.items;
}

export async function uploadDisclosureAppendix(
  hoaId: number,
  file: File,
): Promise<DisclosureAppendixEntry> {
  const form = new FormData();
  form.append('file', file, file.name);
  const res = await fetch(
    `${BASE_URL}/api/disclosure-package/hoa/${hoaId}/appendices`,
    {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    },
  );
  return handleResponse<DisclosureAppendixEntry>(res);
}

export async function deleteDisclosureAppendix(
  hoaId: number,
  filename: string,
): Promise<void> {
  const res = await fetch(
    `${BASE_URL}/api/disclosure-package/hoa/${hoaId}/appendices/${encodeURIComponent(filename)}`,
    { method: 'DELETE', headers: authHeaders() },
  );
  if (!res.ok) {
    throw new Error(`Failed to delete appendix (${res.status})`);
  }
}
