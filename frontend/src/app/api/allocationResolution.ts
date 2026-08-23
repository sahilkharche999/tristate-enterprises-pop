import { BASE_URL } from './config';
import { authHeaders, handleResponse } from './http';

export type AllocationResolutionState = {
  property_id: number;
  assessment_setup_id: number;
  resolutions: Array<Record<string, unknown>>;
  assessment_categories: Array<{
    pool_key: string;
    pool_name: string;
    allocation_method: string;
    recipient_scope: string;
    budget_line_derivation: string;
  }>;
  slices: Array<Record<string, unknown>>;
  category_decisions: Array<Record<string, unknown>>;
  candidate_factors: {
    ownership_percentage: Record<string, string>;
    square_footage: Record<string, string>;
    custom: Record<string, string>;
  };
  units: Array<{ unit_number: string; square_feet?: string | number | null; ownership_percent?: string | number | null }>;
  approved_schedules: Array<{ id: number; fiscal_year: number; status: string }>;
  readiness: {
    ready_for_final: boolean;
    preview_available: boolean;
    enforcement: string;
    issues: Array<{
      code: string;
      severity: string;
      message: string;
      target: string;
      fix_path: string;
      fix_label: string;
      details?: Record<string, unknown>;
    }>;
    gates: Array<{ id: string; ok: boolean; count: number }>;
  };
  blocks_final: boolean;
};

export async function getAllocationResolution(hoaId: number): Promise<AllocationResolutionState> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/allocation-resolution`, {
    headers: authHeaders(),
  });
  return handleResponse<AllocationResolutionState>(res);
}

export async function draftAllocationResolution(
  hoaId: number,
  poolKey: string,
  body: Record<string, unknown>,
) {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/allocation-resolution/pools/${encodeURIComponent(poolKey)}/draft`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
    },
  );
  return handleResponse(res);
}

export async function approveAllocationResolution(
  hoaId: number,
  poolKey: string,
  body: Record<string, unknown>,
) {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/allocation-resolution/pools/${encodeURIComponent(poolKey)}/approve`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
    },
  );
  return handleResponse(res);
}

export async function saveAllocationSlices(hoaId: number, body: Record<string, unknown>) {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/allocation-resolution/slices`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  return handleResponse(res);
}

export async function saveCategoryDecision(hoaId: number, body: Record<string, unknown>) {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/allocation-resolution/categories`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  return handleResponse(res);
}

export async function getAllocationPreview(hoaId: number) {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/allocation-resolution/preview`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}
