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

export async function downloadDisclosurePackagePdf(jobId: string): Promise<Blob> {
  const res = await fetch(disclosurePackageDownloadUrl(jobId), {
    headers: authHeaders(),
  });
  return handleBlobResponse(res);
}
