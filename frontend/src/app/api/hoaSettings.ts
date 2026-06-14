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
  due_date: string;          // free text or ISO date
  amount_per_unit: number;   // dollars; cents preserved
  frequency: string;         // 'month' | 'year' | 'one-time' | free text
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
