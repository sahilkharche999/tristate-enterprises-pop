import { BASE_URL } from './config';
import { authHeaders, handleResponse } from './http';

export type MappingCounts = Record<string, number>;

export interface MappingRule {
  id: number;
  pool_key: string;
  match_label: string | null;
  normalized_label: string | null;
  account_code: string | null;
  match_type: string;
  rule_source: string;
  approval_status: string;
  review_state: string;
  confidence: number | null;
  budget_line_derivation: string;
  source_parent_category?: string | null;
  assessment_type?: string | null;
  review_required?: boolean;
  review_reason?: string | null;
  source_evidence_text?: string | null;
}

export interface LineReviewCandidate {
  rule_id: number;
  pool_key: string;
  pool_name: string;
  score: number;
  match_reason: string;
  decision_level: string;
  source_pages: number[];
  source_evidence_text: string;
  review_reason: string;
  match_label: string;
  rule_source: string;
  budget_line_derivation?: string;
}

export interface LineReviewItem {
  line_key?: string;
  line_label: string;
  normalized_label: string;
  section: string;
  category: string;
  fund_type: string;
  account_code: string | null;
  amount: number | null;
  eligibility: string;
  reason: string;
  status: string;
  pool_key: string | null;
  assessment_mapping_amount?: number | null;
  source_column_used?: string;
  row_role?: string;
  included_in_regular_basis?: boolean;
  candidates: LineReviewCandidate[];
}

export interface ReviewRowPoolOption {
  pool_key: string;
  pool_name: string;
}

export interface ReviewRow {
  line_key: string;
  line_label: string;
  normalized_label: string;
  section: string;
  category: string;
  fund_type: string;
  account_code: string | null;
  assessment_mapping_amount: number | null;
  source_column_used: string;
  amount: number | null;
  row_role: string;
  eligibility: string;
  included_in_regular_basis: boolean;
  reason: string;
  status: string;
  current_status: string;
  disposition_state: string;
  disposition_note: string;
  pool_key: string | null;
  current_pool_key: string | null;
  stale_pool_mapping?: boolean;
  mapping_source: string | null;
  review_state: string | null;
  valid_pool_options: ReviewRowPoolOption[];
  recommended_pool_key: string | null;
  candidates: LineReviewCandidate[];
}

export interface EligibilityLine {
  line_label: string;
  amount: number | null;
  requires_mapping: boolean;
  reason: string;
  canonical?: boolean;
  eligibility?: string;
}

export interface MappingReviewState {
  property_id: number;
  assessment_setup_id: number;
  budget_year: number | null;
  budget_draft_id: number | null;
  pools: Array<{
    pool_key: string;
    pool_name: string;
    allocation_method: string;
    recipient_scope: string;
    budget_line_derivation: string;
  }>;
  rules: MappingRule[];
  aliases: Array<{
    id: number;
    pool_key: string;
    dre_label: string;
    budget_label: string;
    approval_status: string;
  }>;
  existing_mappings: Array<{
    budget_line_normalized_label: string;
    pool_key: string;
    mapping_source: string;
    review_state: string;
    budget_line_amount: number | null;
  }>;
  review_rows: ReviewRow[];
  eligibility_groups: Record<string, EligibilityLine[]>;
  line_review_items: LineReviewItem[];
  residual_preview: {
    candidate_lines: EligibilityLine[];
    excluded_lines: EligibilityLine[];
    unresolved_lines: EligibilityLine[];
  };
  exemption_decisions: Array<{
    pool_key: string;
    exemption_state: string;
    budget_year: number | null;
    notes: string | null;
  }>;
  reconciliation_status: {
    mapped_pool_total: number;
    assessment_target: number;
    passed: boolean;
    failures: string[];
  };
  reconciliation_summary: {
    mapped_regular_total: number;
    pending_split_total: number;
    excluded_non_regular_total: number;
    target_regular_assessment_basis: number;
    difference: number;
    reconciliation_failures: string[];
    unresolved_required_rows: string[];
    final_render_blocked: boolean;
  };
  mapping_review_blockers: Record<string, string[]>;
  progress: { unresolved_count: number };
}

export interface ApplyMappingResponse {
  assessment_setup_id: number;
  counts: MappingCounts;
  line_results: Array<{
    line_label: string;
    eligibility: string;
    requires_mapping: boolean;
    status: string;
    pool_key: string | null;
    mapping_source: string | null;
    reason: string;
  }>;
}

export interface AnalysisEvidenceRef {
  source_type: string;
  rule_id?: number | null;
  alias_id?: number | null;
  pool_key?: string | null;
  page_numbers: number[];
}

export interface AnalysisSafeToStageItem {
  line_label: string;
  normalized_label: string;
  section: string;
  category: string;
  fund_type: string;
  account_code: string | null;
  suggested_pool_key: string;
  action_kind: string;
  confidence: number;
  explanation: string;
  evidence_refs: AnalysisEvidenceRef[];
}

export interface AnalysisDecisionOption {
  pool_key: string;
  label: string;
}

export interface AnalysisNeedsDecisionItem {
  subject_type: string;
  line_label: string;
  normalized_label: string;
  section: string;
  category: string;
  fund_type: string;
  account_code: string | null;
  pool_key: string | null;
  options: AnalysisDecisionOption[];
  recommended_pool_key: string | null;
  explanation: string;
  evidence_refs: AnalysisEvidenceRef[];
  blocker_kind: string;
}

export interface AnalysisExcludeItem {
  line_label: string;
  normalized_label: string;
  section: string;
  category: string;
  fund_type: string;
  account_code: string | null;
  exclusion_kind: string;
  explanation: string;
  evidence_refs: AnalysisEvidenceRef[];
}

export interface AnalysisResidualLine {
  line_label: string;
  normalized_label: string;
  section: string;
  category: string;
  fund_type: string;
  account_code: string | null;
  amount: number | null;
  reason: string;
}

export interface MappingReviewAnalysis {
  available: boolean;
  reasons: string[];
  safe_to_stage: AnalysisSafeToStageItem[];
  needs_decision: AnalysisNeedsDecisionItem[];
  exclude_from_mapping: AnalysisExcludeItem[];
  residual_equal_preview: {
    residual_pool_key: string | null;
    candidate_lines: AnalysisResidualLine[];
    blocked_lines: AnalysisResidualLine[];
    explanation: string;
  };
  audit: {
    model_name: string;
    prompt_version: string;
    prompt_sha256: string;
  };
}

export async function getAssessmentMappingReview(hoaId: number): Promise<MappingReviewState> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review`, {
    headers: authHeaders(),
  });
  return handleResponse<MappingReviewState>(res);
}

export async function approveMappingRule(hoaId: number, ruleId: number, note = '') {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review/rules/${ruleId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ note }),
  });
  return handleResponse(res);
}

export async function rejectMappingRule(hoaId: number, ruleId: number, note = '') {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review/rules/${ruleId}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ note }),
  });
  return handleResponse(res);
}

export async function disableMappingRule(hoaId: number, ruleId: number, note = '') {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review/rules/${ruleId}/disable`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ note }),
  });
  return handleResponse(res);
}

export async function editMappingRule(
  hoaId: number,
  ruleId: number,
  payload: { pool_key: string; match_label?: string; account_code?: string; match_type: string; note?: string },
) {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review/rules/${ruleId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(payload),
  });
  return handleResponse(res);
}

export async function applyAssessmentMappings(hoaId: number): Promise<ApplyMappingResponse> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review/apply`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return handleResponse<ApplyMappingResponse>(res);
}

export async function analyzeAssessmentMappingReview(hoaId: number): Promise<MappingReviewAnalysis> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({}),
  });
  return handleResponse<MappingReviewAnalysis>(res);
}

export async function approveResidualRouting(hoaId: number, note = '') {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review/residual/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ note }),
  });
  return handleResponse(res);
}

export async function createMappingAlias(
  hoaId: number,
  payload: { pool_key: string; dre_label: string; budget_label: string; note?: string },
) {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review/aliases`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(payload),
  });
  return handleResponse(res);
}

export async function approveLineSuggestion(
  hoaId: number,
  payload: {
    rule_id: number;
    line_label: string;
    normalized_label: string;
    section: string;
    category: string;
    fund_type: string;
    account_code?: string | null;
    note?: string;
  },
) {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review/lines/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(payload),
  });
  return handleResponse(res);
}

export async function assignAssessmentMappingReviewRow(
  hoaId: number,
  payload: {
    line_key: string;
    pool_key: string;
    note?: string;
  },
) {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review/rows/assign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(payload),
  });
  return handleResponse(res);
}

export async function setAssessmentMappingReviewRowDisposition(
  hoaId: number,
  payload: {
    line_key: string;
    disposition_state: 'excluded_non_regular' | 'reserve_detail' | 'pending_split' | 'clear';
    note?: string;
  },
) {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review/rows/disposition`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(payload),
  });
  return handleResponse(res);
}

export async function revokeMappingAlias(hoaId: number, aliasId: number, note = '') {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review/aliases/${aliasId}/revoke`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ note }),
  });
  return handleResponse(res);
}

export async function setExemptionDecision(
  hoaId: number,
  poolKey: string,
  payload: { exemption_state: 'active' | 'inactive' | 'pending_review'; budget_year?: number; note?: string },
) {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/assessment-mapping-review/exemptions/${encodeURIComponent(poolKey)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(payload),
  });
  return handleResponse(res);
}
