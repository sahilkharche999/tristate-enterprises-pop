import { BASE_URL } from './config';
import { authHeaders, handleResponse } from './http';

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
  reserve_cash_balance_eoy_prior: number;
  fund_balance_boy_operations: number;
  monthly_assessment_per_unit_prior: number;
  interest_rate_after_tax: number;
  replacement_cost_increase_rate: number;
  assessment_increase_schedule_json: string | null;
  letter_signed_by: string | null;
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
