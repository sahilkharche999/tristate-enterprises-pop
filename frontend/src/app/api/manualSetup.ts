// Manual assessment setup entry — for a property with no DRE/CC&R
// extraction run on file. Creates a synthetic extraction run that the
// operator then reviews and approves through the existing DRE/CC&R
// Review Workbench, exactly like any Gemini-derived run.
//
//   POST /hoa/{hoa_id}/assessment-setup/manual → ManualExtractionRunResponse

import { BASE_URL } from './config';
import { authHeaders, handleResponse } from './http';

export type PromptSetupType =
  | 'fixed_equal'
  | 'grouped_category'
  | 'individual_unit'
  | 'multi_pool_combination'
  | 'unknown_needs_review';

export type PromptAllocationMethod =
  | 'equal'
  | 'square_footage'
  | 'ownership_percentage'
  | 'category'
  | 'specified_value'
  | 'parking_space'
  | 'custom_factor'
  | 'unknown';

export interface ManualPoolEntry {
  pool_key: string;
  pool_name?: string;
  annual_amount?: number | null;
  allocation_method: PromptAllocationMethod;
  recipient_scope?: string;
  denominator_value?: number | null;
  variable_flag?: boolean;
}

export interface ManualGroupEntry {
  group_id?: string;
  label?: string;
  unit_count: number;
  average_square_feet?: number | null;
  ownership_percent?: number | null;
}

export interface ManualUnitEntry {
  unit_number: string;
  square_feet?: number | null;
  ownership_percent?: number | null;
  category?: string;
  parking_spaces?: number;
}

export interface ManualExtractionRunResponse {
  dre_document_id: number;
  extraction_run_id: number;
  property_id: number;
}

export async function createManualAssessmentSetup(
  hoaId: number,
  body: {
    setup_type: PromptSetupType;
    pools: ManualPoolEntry[];
    groups?: ManualGroupEntry[];
    units?: ManualUnitEntry[];
  },
): Promise<ManualExtractionRunResponse> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-setup/manual`, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return handleResponse(res);
}
