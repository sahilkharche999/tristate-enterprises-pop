import { BASE_URL } from './config';
import { authHeaders, handleResponse } from './http';
import type { AssessmentMode } from '../lib/assessmentMode';

export interface HOARecord {
  id: number;
  hoa_code: string;
  name: string;
  tax_id: string;
  units: number;
  reserve_inflation_rate: number;
  fiscal_year_start_month: number;
  fiscal_year_end_month: number;
  city: string;
  portfolio_year: number | null;
  workflow_status: string;
  assessment_mode: AssessmentMode;
  created_at?: string | null;
}

export interface HOAUpdatePayload {
  name: string;
  hoa_code?: string;
  tax_id?: string;
  units?: number;
  fiscal_year_start_month: number;
  city?: string;
  assessment_mode?: AssessmentMode;
}

export interface HOACreatePayload {
  name: string;
  // Optional at create time — populated from the DRE extraction once the
  // operator approves the run. Backend defaults to 0 when omitted.
  units?: number;
  fiscal_year_start_month: number;
  city?: string;
  assessment_mode?: AssessmentMode;
}

export async function listHOAs(): Promise<HOARecord[]> {
  const res = await fetch(`${BASE_URL}/hoa`, { headers: authHeaders() });
  return handleResponse<HOARecord[]>(res);
}

export async function createHOA(payload: HOACreatePayload): Promise<HOARecord> {
  const res = await fetch(`${BASE_URL}/hoa`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(payload),
  });
  return handleResponse<HOARecord>(res);
}

export async function getHOA(id: number | string): Promise<HOARecord> {
  const res = await fetch(`${BASE_URL}/hoa/${id}`, { headers: authHeaders() });
  return handleResponse<HOARecord>(res);
}

export async function updateHOA(id: number | string, payload: HOAUpdatePayload): Promise<HOARecord> {
  const res = await fetch(`${BASE_URL}/hoa/${id}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(payload),
  });
  return handleResponse<HOARecord>(res);
}
