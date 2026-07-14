import { BASE_URL } from './config';
import { authHeaders, handleResponse } from './http';

// Phase 4.4 status (per dre-driven-assessment-engine prompts.md matrix):
//   - 'none' or undefined → historical "no anticipated SA" wording
//   - 'approved_scheduled' → board has approved + scheduled the SA;
//     amount/due_date/included_in_regular_monthly are required for
//     the cover letter §5300 disclosure + §5570 table
//   - 'possible_disclosure_only' → no charge yet; display_language
//     renders verbatim in the cover letter
export type SpecialAssessmentStatus =
  | 'none'
  | 'approved_scheduled'
  | 'possible_disclosure_only';

export interface SpecialAssessmentEntry {
  due_date: string;          // MM/DD/YYYY (task 5.1/5.5)
  amount_per_unit: number;   // dollars; cents preserved
  // `frequency` removed from the form (task 5.3) — a special assessment is
  // inherently one-time. Legacy stored entries may still carry a
  // `frequency` key; the backend ignores it rather than erroring.
  purpose: string;
  // Phase 4.4 extensions — optional for backward compatibility with
  // pre-Phase-4 saved data; backend `infer_special_assessment_status`
  // falls back to 'approved_scheduled' for legacy rows that have
  // amount+due_date but no explicit status.
  status?: SpecialAssessmentStatus;
  label?: string;            // operator-facing name (e.g. "Pool deck repair")
  included_in_regular_monthly?: boolean;
  display_language?: string; // free-text wording for disclosure_only
  recipient_scope?:
    | 'all_units'
    | 'residential_only'
    | 'commercial_only'
    | 'parking_users';
  // Pool-based special assessments (add-variable-special-assessments): link this
  // entry to a special-assessment pool by pool_key. `total_amount` is the
  // operator-entered one-time total used when the pool has no mapped budget line;
  // the engine allocates it across units by the pool's basis.
  pool_key?: string;
  total_amount?: number | null;
  // Manual (pool-free) variable special assessment: the operator picks how the
  // total is split across the HOA's existing units. When set (and no pool_key),
  // the backend allocates directly from the approved setup's per-unit data.
  allocation_basis?: 'equal' | 'square_footage' | 'ownership_percentage';
}

export interface SpecialAssessmentPool {
  pool_key: string;
  pool_name: string;
  allocation_method: string;
  recipient_scope: string;
}

export interface SpecialAssessmentAllocationRow {
  recipient_label: string;
  amount: number;
}

export interface SpecialAssessmentPreview {
  available: boolean;
  reason?: string;
  pool_key?: string;
  allocation_method?: string;
  total?: number | null;
  allocations?: SpecialAssessmentAllocationRow[];
}

export interface OutstandingLoan {
  balance: number;
  lender: string;
  original_amount: number | null;
  interest_rate: number | null;   // decimal fraction (0.045 == 4.5%)
  payoff_date: string | null;
  purpose: string | null;
}

// 30-year reserve funding study (drifting-puzzling-grove rebuild).
export interface AssessmentIncreaseBracket {
  start_year: number;
  end_year: number;
  rate: number;   // decimal (0.03 == 3%)
}

export interface BoardDeferralEntry {
  year: number;
  amount: number;   // dollars
}

export type ReserveFundingSource =
  | 'reserve_study_provision'
  | 'budget_allocation_line'
  | 'manual';

export type FinancialPacketArchetype = 'dual-fund' | 'reserve-only';

export interface HOADisclosureSettings {
  property_id: number;
  management_company: string | null;
  management_company_address: string | null;
  management_company_phone: string | null;
  management_company_fax: string | null;
  management_company_web: string | null;
  cpa_firm_name: string | null;
  cpa_firm_address: string | null;
  reserve_study_expert_name: string | null;
  reserve_study_date: string | null;
  reserve_cash_balance_eoy_prior: number;
  fund_balance_boy_operations: number;
  monthly_assessment_per_unit_prior: number;
  interest_rate_after_tax: number;
  replacement_cost_increase_rate: number;
  assessment_increase_schedule_json: string | null;
  letter_signed_by: string | null;
  // Priority-A disclosure inputs (drifting-puzzling-grove)
  approved_monthly_assessment_per_unit: number | null;
  financial_packet_archetype: FinancialPacketArchetype;
  reserve_interest_income_override: number | null;
  income_tax_provision_override: number | null;
  reserve_funding_source: ReserveFundingSource;
  reserve_funding_manual_amount: number | null;
  special_assessments_json: string;          // JSON-encoded SpecialAssessmentEntry[]
  additional_assessments_needed_json: string; // JSON-encoded SpecialAssessmentEntry[]
  outstanding_loan_json: string | null;       // JSON-encoded OutstandingLoan or null
  // Phase 1 boilerplate-gap fields (drifting-puzzling-grove)
  letter_date: string | null;                 // free text, e.g. "November 18, 2025"
  letter_signed_by_title: string | null;      // e.g. "Vice President, Tri-State Enterprises, Inc."
  accountant_report_date: string | null;
  reserve_funding_plan_date: string | null;
  hoa_state: string;                          // default 'CA'
  hoa_entity_type: string | null;
  hoa_incorporation_year: number | null;
  // 30-year reserve funding study (drifting-puzzling-grove rebuild)
  replacement_fund_monthly_assessment_per_unit: number | null;
  board_deferrals_json: string;                // JSON-encoded BoardDeferralEntry[]
  // Per-HOA disclosure-package logo (fix-disclosure-layout-toc-special-assessment)
  has_logo: boolean;
}

export async function getHOADisclosureSettings(hoaId: number): Promise<HOADisclosureSettings> {
  const r = await fetch(`${BASE_URL}/hoa/${hoaId}/settings/disclosure`, { headers: authHeaders() });
  return handleResponse<HOADisclosureSettings>(r);
}

export async function putHOADisclosureSettings(
  hoaId: number,
  payload: Partial<HOADisclosureSettings>,
): Promise<HOADisclosureSettings> {
  const r = await fetch(`${BASE_URL}/hoa/${hoaId}/settings/disclosure`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(payload),
  });
  return handleResponse<HOADisclosureSettings>(r);
}

export async function listSpecialAssessmentPools(
  hoaId: number,
): Promise<SpecialAssessmentPool[]> {
  const r = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment/special-pools`, {
    headers: authHeaders(),
  });
  const data = await handleResponse<{ pools: SpecialAssessmentPool[] }>(r);
  return data.pools || [];
}

export async function previewSpecialAssessment(
  hoaId: number,
  poolKey: string,
  fiscalYear: number,
): Promise<SpecialAssessmentPreview> {
  const r = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment/special-preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ pool_key: poolKey, fiscal_year: fiscalYear }),
  });
  return handleResponse<SpecialAssessmentPreview>(r);
}

export function hoaLogoUrl(hoaId: number): string {
  return `${BASE_URL}/hoa/${hoaId}/settings/logo`;
}

export async function uploadHOALogo(
  hoaId: number,
  file: File,
): Promise<HOADisclosureSettings> {
  const form = new FormData();
  form.append('file', file);
  const r = await fetch(`${BASE_URL}/hoa/${hoaId}/settings/logo`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  });
  return handleResponse<HOADisclosureSettings>(r);
}

export async function deleteHOALogo(hoaId: number): Promise<HOADisclosureSettings> {
  const r = await fetch(`${BASE_URL}/hoa/${hoaId}/settings/logo`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  return handleResponse<HOADisclosureSettings>(r);
}
