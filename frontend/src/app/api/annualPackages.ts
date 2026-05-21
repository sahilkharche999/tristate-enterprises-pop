// Typed API client for AnnualPackage lifecycle endpoints (Phase 4.8 of
// dre-driven-assessment-engine).
//
// Endpoints (backend at backend/app/routers/annual_packages.py):
//   GET    /hoa/{hoa_id}/annual-packages                          → AnnualPackage[]
//   GET    /hoa/{hoa_id}/annual-packages/{package_id}             → AnnualPackage
//   POST   /hoa/{hoa_id}/annual-packages                          → AnnualPackage (draft)
//   POST   /hoa/{hoa_id}/annual-packages/{package_id}/approve     → AnnualPackage (approved)
//   POST   /hoa/{hoa_id}/annual-packages/{package_id}/finalize    → AnnualPackage (finalized)
//
// State-changing endpoints (approve/finalize) accept an optional ``If-Match``
// header carrying the version_int from the GET response. The backend returns
// 409 Conflict when the row was modified concurrently.

import { BASE_URL } from './config';
import { authHeaders, handleResponse } from './http';

export type PackageStatus =
  | 'draft'
  | 'preflight_failed'
  | 'approved'
  | 'rendered'
  | 'finalized';

export interface AnnualPackage {
  package_id: number;
  property_id: number;
  assessment_setup_id: number | null;
  budget_year: number;
  fiscal_year: number;
  status: PackageStatus;
  approved_assessment_revenue_annual: string | null;
  approved_by: string | null;
  approved_at: string | null;
  finalized_at: string | null;
  regen_of_package_id: number | null;
  version_int: number;
}

export interface CreatePackageRequest {
  budget_year: number;
  fiscal_year: number;
  assessment_setup_id?: number | null;
  regen_of_package_id?: number | null;
}

export interface ApprovePackageRequest {
  approved_assessment_revenue_annual: string;
}

export interface FinalizePackageRequest {
  assessment_setup: unknown;
  budget: unknown;
  reserve: unknown;
  appendix_manifest: unknown;
}

function ifMatchHeader(version: number | undefined): HeadersInit {
  return version != null ? { 'If-Match': String(version) } : {};
}

export async function listAnnualPackages(hoaId: number): Promise<AnnualPackage[]> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/annual-packages`, {
    headers: authHeaders(),
  });
  return handleResponse<AnnualPackage[]>(res);
}

export async function getAnnualPackage(
  hoaId: number,
  packageId: number,
): Promise<AnnualPackage> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/annual-packages/${packageId}`, {
    headers: authHeaders(),
  });
  return handleResponse<AnnualPackage>(res);
}

export async function createAnnualPackage(
  hoaId: number,
  body: CreatePackageRequest,
): Promise<AnnualPackage> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/annual-packages`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return handleResponse<AnnualPackage>(res);
}

export async function approveAnnualPackage(
  hoaId: number,
  packageId: number,
  body: ApprovePackageRequest,
  expectedVersion?: number,
): Promise<AnnualPackage> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/annual-packages/${packageId}/approve`,
    {
      method: 'POST',
      headers: {
        ...authHeaders(),
        ...ifMatchHeader(expectedVersion),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    },
  );
  return handleResponse<AnnualPackage>(res);
}

export async function finalizeAnnualPackage(
  hoaId: number,
  packageId: number,
  body: FinalizePackageRequest,
  expectedVersion?: number,
): Promise<AnnualPackage> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/annual-packages/${packageId}/finalize`,
    {
      method: 'POST',
      headers: {
        ...authHeaders(),
        ...ifMatchHeader(expectedVersion),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    },
  );
  return handleResponse<AnnualPackage>(res);
}
