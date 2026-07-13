// Typed API client for the per-HOA appendix manifest (Phase 5.4 of
// dre-driven-assessment-engine).
//
// Endpoints (backend at backend/app/routers/appendices.py):
//   GET    /hoa/{hoa_id}/appendices                          → AppendixDocument[]
//   POST   /hoa/{hoa_id}/appendices  (multipart)             → AppendixDocument
//   PUT    /hoa/{hoa_id}/appendices/{appendix_id}            → AppendixDocument
//   DELETE /hoa/{hoa_id}/appendices/{appendix_id}            → AppendixDocument (retired)

import { BASE_URL } from './config';
import { authHeaders, handleResponse } from './http';

export type AppendixCadence = 'persistent' | 'annual' | 'one_time';
export type AppendixStatus = 'active' | 'retired';

export interface AppendixDocument {
  appendix_id: number;
  property_id: number;
  file_id: string;
  file_name: string;
  display_title: string;
  default_display_order: number;
  required_flag: boolean;
  include_by_default: boolean;
  cadence: AppendixCadence;
  annual_year: number | null;
  valid_through_year: number | null;
  needs_cadence_review: boolean;
  status: AppendixStatus;
  version_int: number;
  // H7: backing file is gone from storage — re-upload or deselect before it
  // blocks a render.
  file_missing?: boolean;
}

export interface AppendixUpdateRequest {
  expected_version: number;
  display_title?: string;
  default_display_order?: number;
  include_by_default?: boolean;
  cadence?: AppendixCadence;
  annual_year?: number | null;
  valid_through_year?: number | null;
  required_flag?: boolean;
  needs_cadence_review?: boolean;
}

export async function listAppendices(
  hoaId: number,
  options: { includeRetired?: boolean } = {},
): Promise<AppendixDocument[]> {
  const params = new URLSearchParams();
  if (options.includeRetired) params.set('include_retired', 'true');
  const qs = params.toString();
  const url = `${BASE_URL}/hoa/${hoaId}/appendices${qs ? `?${qs}` : ''}`;
  const res = await fetch(url, { headers: authHeaders() });
  return handleResponse<AppendixDocument[]>(res);
}

export async function uploadAppendix(
  hoaId: number,
  args: {
    file: File;
    displayTitle: string;
    cadence?: AppendixCadence;
    annualYear?: number | null;
    validThroughYear?: number | null;
    requiredFlag?: boolean;
    includeByDefault?: boolean;
  },
): Promise<AppendixDocument> {
  const form = new FormData();
  form.append('file', args.file);
  form.append('display_title', args.displayTitle);
  form.append('cadence', args.cadence ?? 'persistent');
  if (args.annualYear != null) form.append('annual_year', String(args.annualYear));
  if (args.validThroughYear != null)
    form.append('valid_through_year', String(args.validThroughYear));
  if (args.requiredFlag != null) form.append('required_flag', String(args.requiredFlag));
  if (args.includeByDefault != null)
    form.append('include_by_default', String(args.includeByDefault));

  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/appendices`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  });
  return handleResponse<AppendixDocument>(res);
}

export async function updateAppendix(
  hoaId: number,
  appendixId: number,
  body: AppendixUpdateRequest,
): Promise<AppendixDocument> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/appendices/${appendixId}`, {
    method: 'PUT',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return handleResponse<AppendixDocument>(res);
}

export async function retireAppendix(
  hoaId: number,
  appendixId: number,
): Promise<AppendixDocument> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/appendices/${appendixId}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  return handleResponse<AppendixDocument>(res);
}

export async function downloadAnnualAppendix(
  hoaId: number,
  appendixId: number,
  fileName: string,
): Promise<void> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/appendices/${appendixId}/download`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Download failed: ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(url);
}
