import { BASE_URL } from './config.ts';
import { authHeaders, handleBlobResponse, handleResponse } from './http.ts';
import type { LineItem } from '../data/mockData.ts';
import {
  createBudgetBundleFormData,
  createBudgetSourceFormData,
  type BudgetSourceMode,
} from '../lib/budgetSourceMode.ts';
import type { AssessmentMode } from '../lib/assessmentMode.ts';

type JsonValue = string | number | boolean | null | JsonObject | JsonValue[];
type JsonObject = Record<string, JsonValue>;

export interface BudgetTimelineEvent {
  id: number;
  event_type: string;
  summary: string;
  actor_name: string;
  occurred_at: string;
  related_upload_id?: number | null;
  related_draft_id?: number | null;
  related_version_id?: number | null;
  related_note_id?: number | null;
  file_name?: string | null;
  version_code?: string | null;
  payload?: JsonObject | null;
}

export interface BudgetVersionSummary {
  id: number;
  version_number: number;
  version_code: string;
  stage: string;
  label?: string | null;
  summary_note?: string | null;
  total_income: number;
  total_expense: number;
  net_operating_income: number;
  growth_factor?: number | null;
  growth_factor_note?: string | null;
  reserve_inflation_rate: number;
  reserve_inflation_note?: string | null;
  statement_month?: number | null;
  created_at: string;
  created_by_name: string;
  source_draft_id?: number | null;
  output_storage_key?: string | null;
  source_upload_filename?: string | null;
  source_mode?: BudgetSourceMode | null;
  assessment_mode?: AssessmentMode | null;
}

export interface BudgetVersionDetail extends BudgetVersionSummary {
  source_upload_id?: number | null;
  source_draft_id?: number | null;
  reopened_from_version_id?: number | null;
  fiscal_year_start_month: number;
  fiscal_year_end_month: number;
  line_items: JsonObject[];
  budget_preview?: JsonObject | null;
}

export interface BudgetNoteRecord {
  id: number;
  note_scope: string;
  line_item_key?: string | null;
  title: string;
  body: string;
  created_at: string;
  created_by_name: string;
  upload_id?: number | null;
  draft_id?: number | null;
  version_id?: number | null;
}

export interface BudgetDraftPayload {
  id: number;
  status: string;
  source_upload_id?: number | null;
  reserve_study_upload_id?: number | null;
  reopened_from_version_id?: number | null;
  line_items: JsonObject[];
  reserve_study_status?: string;
  reserve_study_rows: JsonObject[];
  reserve_study_warnings: string[];
  global_note?: string | null;
  statement_month?: number | null;
  growth_factor?: number | null;
  growth_factor_note?: string | null;
  reserve_inflation_rate: number;
  reserve_inflation_note?: string | null;
  version_int: number;
  updated_at?: string | null;
  upload_filename?: string | null;
  enriched_file_available?: boolean | null;
  source_mode?: BudgetSourceMode | null;
  assessment_mode?: AssessmentMode | null;
}

export interface BudgetDraftSummary {
  id: number;
  status: string;
  source_upload_id?: number | null;
  reserve_study_upload_id?: number | null;
  source_upload_filename?: string | null;
  reopened_from_version_id?: number | null;
  reopened_from_version_code?: string | null;
  reserve_inflation_rate: number;
  reserve_inflation_note?: string | null;
  version_int: number;
  reserve_study_status?: string;
  updated_at?: string | null;
  actor_name: string;
  enriched_file_available?: boolean | null;
  source_mode?: BudgetSourceMode | null;
  assessment_mode?: AssessmentMode | null;
}

export interface BudgetHistoryResponse {
  active_draft?: BudgetDraftPayload | null;
  drafts: BudgetDraftSummary[];
  timeline: BudgetTimelineEvent[];
  versions: BudgetVersionSummary[];
  notes: BudgetNoteRecord[];
}

export interface BudgetGlIdentityPayload {
  account_code?: string | null;
  label: string;
  normalized_label?: string | null;
  line_item_key?: string | null;
  section: string;
  category: string;
  fund_type: string;
}

export interface BudgetGlMergeCommitRequest {
  primary: BudgetGlIdentityPayload;
  secondary: BudgetGlIdentityPayload;
  source: 'manual' | 'gemini_suggestion';
}

export interface BudgetGlMergeApplicationPayload {
  id: number;
  merge_id: number;
  property_id: number;
  budget_draft_id: number;
  assessment_setup_id?: number | null;
  source: string;
  status: string;
  match_strategy?: string | null;
}

export interface BudgetGlMergeCommitResponse {
  merge_id: number;
  application: BudgetGlMergeApplicationPayload;
  draft_version: number;
}

export interface BudgetGlMergeListItem {
  id: number;
  property_id: number;
  primary_account_code?: string | null;
  primary_label: string;
  primary_normalized_label: string;
  secondary_account_code?: string | null;
  secondary_label: string;
  secondary_normalized_label: string;
  status: string;
  application_id?: number | null;
  application_status?: string | null;
  source?: string | null;
}

export interface BudgetGlMergeSuggestionPayload {
  primary_account_code?: string | null;
  secondary_account_code?: string | null;
  primary_label: string;
  secondary_label: string;
  primary_normalized_label: string;
  secondary_normalized_label: string;
  confidence: number;
  reason: string;
  local_only: boolean;
  wire_schema_sha256: string;
}

export interface ExtractionQualityWarning {
  code: string;
  title: string;
  body: string;
  severity: 'warning' | 'info';
}

export interface ExtractionDebugInfo {
  code: string;
  message: string;
  details: JsonObject;
}

export interface BudgetUploadResponse {
  upload_id: number;
  // Null when the backend decides the extraction needs manual review (PDF
  // extraction failed, or the parsed statement failed confidence checks).
  // Callers MUST check for null and fall back to review_reason / warnings.
  draft: BudgetDraftPayload | null;
  timeline_event: BudgetTimelineEvent;
  warnings?: string[];
  review_required?: boolean;
  review_reason?: string | null;
  debug_info?: ExtractionDebugInfo | null;
  // Set when the extractor took a degraded path (e.g. scanned-PDF
  // vision-only fallback). The frontend should render this as a one-shot
  // dismissible dialog so the user knows to double-check the numbers.
  extraction_quality_warning?: ExtractionQualityWarning | null;
}

export interface ReserveStudyRow {
  row_id: string;
  row_type?: 'item' | 'header';
  line_item: string;
  useful_life: number | null;
  remaining_life: number | null;
  quantity: string | null;
  replacement_cost: number | null;
  year_new?: number | null;
  reference_year?: number | null;
  year_replacement_provision?: number | null;
  estimated_liability?: number | null;
  source_page?: number | null;
  flags?: string[];
}

export interface BundleFileStatus {
  upload_id?: number | null;
  filename?: string | null;
  status: 'completed' | 'review_required' | 'failed' | 'pending';
  warnings: string[];
  review_reason?: string | null;
  debug_info?: ExtractionDebugInfo | null;
}

export interface BudgetBundleUploadResponse {
  draft: BudgetDraftPayload | null;
  budget_source: BundleFileStatus;
  reserve_study: BundleFileStatus;
  can_continue_with_budget_only: boolean;
  can_continue_with_reserve_study_only: boolean;
}

export interface SaveReserveStudyPayload {
  rows: JsonObject[];
  warnings?: string[];
}

export interface ApplyReserveStudyResponse {
  draft: BudgetDraftPayload;
  applied_count: number;
  message: string;
}

export interface SaveBudgetDraftPayload {
  draft_id: number;
  line_items: JsonObject[];
  global_note?: string | null;
  statement_month?: number | null;
  growth_factor?: number | null;
  growth_factor_note?: string | null;
  reserve_inflation_rate?: number;
  reserve_inflation_note?: string | null;
}

export interface SaveBudgetDraftResponse {
  draft: BudgetDraftPayload;
  timeline_event?: BudgetTimelineEvent | null;
}

export interface SaveBudgetNotePayload {
  draft_id: number;
  version_id?: number | null;
  note_scope: string;
  line_item_key?: string | null;
  title: string;
  body: string;
}

export interface SaveBudgetNoteResponse {
  note: BudgetNoteRecord;
  timeline_event: BudgetTimelineEvent;
}

export interface GenerateBudgetVersionPayload {
  draft_id: number;
  line_items: JsonObject[];
  global_note?: string | null;
}

export interface GenerateBudgetVersionResponse {
  draft: BudgetDraftPayload;
  version: BudgetVersionDetail;
  timeline_event: BudgetTimelineEvent;
}

export interface BudgetDraftCompareBaselineOption {
  id: number;
  version_code: string;
  stage: string;
  label?: string | null;
  created_at: string;
  summary_note?: string | null;
  is_recommended: boolean;
}

export interface BudgetDraftCompareOptionsResponse {
  draft_id: number;
  recommended_baseline_version_id?: number | null;
  recommended_baseline_reason?: string | null;
  baseline_versions: BudgetDraftCompareBaselineOption[];
  reserve_inflation_rate: number;
  reserve_inflation_note?: string | null;
}

export interface BudgetDraftCompareRequest {
  baseline_version_id: number;
  changed_only: boolean;
}

export interface BudgetDraftCompareSectionSummary {
  baseline_amount: number;
  inflation_adjusted_baseline_amount: number;
  current_amount: number;
  delta_amount: number;
  delta_percent?: number | null;
}

export interface BudgetDraftChangeSummary {
  baseline_amount: number;
  current_amount: number;
  delta_amount: number;
  delta_percent?: number | null;
}

export interface BudgetReserveReviewSummary {
  baseline_amount: number;
  inflation_adjusted_amount: number;
  impact_amount: number;
  eligible_component_count: number;
}

export interface BudgetDraftCompareRow {
  line_item_key: string;
  label: string;
  category: string;
  reserve_group?: string | null;
  is_reserve: boolean;
  reserve_inflation_eligible: boolean;
  changed: boolean;
  baseline_amount: number;
  inflation_adjusted_baseline_amount: number;
  current_amount: number;
  delta_amount: number;
  delta_percent?: number | null;
  note_title?: string | null;
  note_body?: string | null;
}

export interface BudgetReserveComponentRow {
  line_item_key: string;
  label: string;
  baseline_amount: number;
  inflation_adjusted_amount: number;
  impact_amount: number;
}

export interface BudgetDraftCompareResponse {
  draft_id: number;
  baseline_version_id: number;
  changed_only: boolean;
  draft_compare_summary: BudgetDraftChangeSummary;
  draft_compare_rows: BudgetDraftCompareRow[];
  reserve_review_summary: BudgetReserveReviewSummary;
  reserve_component_rows: BudgetReserveComponentRow[];
}

export interface BudgetVersionCompareCard {
  id: number;
  version_code: string;
  stage: string;
  label?: string | null;
  summary_note?: string | null;
  created_at: string;
  created_by_name: string;
  source_upload_filename?: string | null;
  total_income: number;
  total_expense: number;
  net_operating_income: number;
  growth_factor?: number | null;
  growth_factor_note?: string | null;
  statement_month?: number | null;
  fiscal_year_start_month: number;
  fiscal_year_end_month: number;
  source_mode?: BudgetSourceMode | null;
  assessment_mode?: AssessmentMode | null;
}

export interface BudgetVersionCompareResponse {
  versions: BudgetVersionCompareCard[];
}

export interface ReopenBudgetVersionResponse {
  draft: BudgetDraftPayload;
  timeline_event: BudgetTimelineEvent;
}

export interface UpdateBudgetVersionMetadataPayload {
  stage?: 'Interim' | 'Final';
  label?: string | null;
  summary_note?: string | null;
}

export interface UpdateBudgetVersionMetadataResponse {
  version: BudgetVersionDetail;
  timeline_events: BudgetTimelineEvent[];
}

function toNumber(value: JsonValue | undefined): number {
  return typeof value === 'number' ? value : Number(value ?? 0) || 0;
}

function pickString(...values: Array<JsonValue | undefined>): string | undefined {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value;
    }
  }
  return undefined;
}

function readRecord(value: JsonValue | undefined): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as JsonObject) : {};
}

function inferCategory(record: JsonObject, label: string, accountCode?: number): LineItem['category'] {
  const explicit = pickString(record.category, record.category_name, readRecord(record.raw).Category);
  // Accept the canonical 4-value taxonomy directly.
  if (
    explicit === 'income' ||
    explicit === 'operating' ||
    explicit === 'reserve_income' ||
    explicit === 'reserve_expense'
  ) {
    return explicit;
  }
  // Legacy: coerce deprecated 3-value "reserve" → "reserve_expense".
  // Retained for one release cycle while drafts migrate to the 4-value taxonomy.
  if (explicit === 'reserve') {
    return 'reserve_expense';
  }

  const normalizedLabel = (label || '').toLowerCase();
  if (
    normalizedLabel.includes('income') ||
    normalizedLabel.includes('revenue') ||
    normalizedLabel.includes('assessment') ||
    normalizedLabel.includes('late fee') ||
    normalizedLabel.includes('interest')
  ) {
    return 'income';
  }
  // Account-code ranges aligned with backend _classify_by_account_code.
  if (accountCode != null && accountCode >= 90000) {
    return 'reserve_expense';
  }
  if (accountCode != null && accountCode < 50000) {
    return 'income';
  }
  return 'operating';
}

export function budgetHistoryLineItemToEditorItem(record: JsonObject, index: number): LineItem {
  const raw = readRecord(record.raw);
  const accountCodeValue = record.account_code ?? raw['Account Code'] ?? raw.Account;
  // Handle hyphenated PDF account codes (e.g. "41-4110" → 414110)
  const accountCodeStr = accountCodeValue == null ? null : String(accountCodeValue).replace(/[-.\s]/g, '');
  let accountCode = accountCodeStr ? (Number(accountCodeStr) || undefined) : undefined;
  // Generate synthetic account code from label hash for PDFs without account codes
  if (accountCode == null) {
    const labelForHash = pickString(record.label, record.name, raw.Label, raw['Account Name'], raw.Description) ?? '';
    if (labelForHash) {
      let hash = 0;
      for (let i = 0; i < labelForHash.length; i++) {
        hash = ((hash << 5) - hash + labelForHash.charCodeAt(i)) | 0;
      }
      accountCode = 90000 + (Math.abs(hash) % 9999);
    }
  }
  const label =
    pickString(record.label, record.name, raw.Label, raw['Account Name'], raw.Description) ??
    `Line Item ${index + 1}`;
  const annualBudget = toNumber(record.annualBudget ?? record.annual_budget ?? raw['Annual Budget']);
  const projection = toNumber(record.projection ?? raw.Projection);
  const ytdActual = toNumber(record.ytdActual ?? record.ytd_actual ?? raw['YTD Actual']);
  const currentPeriod = toNumber(record.currentPeriod ?? record.current_period ?? raw['Current Period']);
  const variance = toNumber(record.variance ?? raw.Variance);
  const proposedAmount = toNumber(record.proposedAmount ?? record.proposed_amount);
  const percentChangeValue = record.percentChange ?? record.percent_change;
  const percentChange =
    typeof percentChangeValue === 'number' || typeof percentChangeValue === 'string'
      ? Number(percentChangeValue) || 0
      : annualBudget > 0 && proposedAmount > 0
        ? ((proposedAmount / annualBudget) - 1) * 100
        : 0;

  return {
    id: pickString(record.id) ?? `${record.line_item_key ?? accountCode ?? label}-${index}`,
    category: inferCategory(record, label, accountCode),
    name: label,
    ytdActual,
    annualBudget,
    percentChange,
    projection,
    currentPeriod,
    variance,
    readOnly: Boolean(record.readOnly ?? record.read_only),
    accountCode,
    label,
    lineItemKey: pickString(record.line_item_key) ?? null,
    normalizedLabel: pickString(record.normalized_label) ?? null,
    fundType: pickString(record.fund_type, raw.fund_type) ?? null,
    mergedCount: toNumber(record.merged_count) || 0,
    mergedGls: Array.isArray(record.merged_gls)
      ? record.merged_gls.map((entry) => {
          const mergeRecord = readRecord(entry as JsonValue);
          const contributions = readRecord(mergeRecord.contributions);
          return {
            accountCode: pickString(mergeRecord.account_code) ?? null,
            label: pickString(mergeRecord.label) ?? null,
            lineItemKey: pickString(mergeRecord.line_item_key) ?? null,
            normalizedLabel: pickString(mergeRecord.normalized_label) ?? null,
            contributions: {
              current_period: toNumber(contributions.current_period),
              ytd: toNumber(contributions.ytd),
              annual_budget: toNumber(contributions.annual_budget),
              projection: toNumber(contributions.projection),
              variance: toNumber(contributions.variance),
            },
          };
        })
      : [],
    reserveGroup: pickString(record.reserve_group, record.reserveGroup) as
      | 'component'
      | 'income'
      | 'transfer'
      | undefined,
    rawSection: pickString(readRecord(record.raw).section, record.section),
    sourceColumn: pickString(record.source_column, record.sourceColumn) ?? null,
    sourcePageOrCell: pickString(record.source_page_or_cell, record.sourcePageOrCell) ?? null,
    note:
      record.note && typeof record.note === 'object' && !Array.isArray(record.note)
        ? {
            title: pickString((record.note as JsonObject).title) ?? '',
            body: pickString((record.note as JsonObject).body) ?? '',
          }
        : undefined,
  };
}

export function mapBudgetHistoryLineItems(records: JsonObject[]): LineItem[] {
  return records.map((record, index) => budgetHistoryLineItemToEditorItem(record, index));
}

export function mapEditorLineItemsToBudgetHistory(lineItems: LineItem[]): JsonObject[] {
  return lineItems.map((item) => ({
    id: item.id,
    category: item.category,
    name: item.name,
    label: item.label ?? item.name,
    line_item_key: item.lineItemKey ?? String(item.accountCode ?? item.label ?? item.name),
    normalized_label: item.normalizedLabel ?? null,
    account_code: item.accountCode ?? null,
    ytdActual: item.ytdActual,
    ytd_actual: item.ytdActual,
    current_period: item.currentPeriod ?? null,
    annualBudget: item.annualBudget,
    annual_budget: item.annualBudget,
    percentChange: item.percentChange,
    percent_change: item.percentChange,
    projection: item.projection ?? null,
    variance: item.variance ?? null,
    readOnly: item.readOnly ?? false,
    read_only: item.readOnly ?? false,
    reserve_group: item.reserveGroup ?? null,
    section: item.rawSection ?? null,
    fund_type: item.fundType ?? null,
    merged_count: item.mergedCount ?? item.mergedGls?.length ?? 0,
    merged_gls: (item.mergedGls ?? []).map((merged) => ({
      account_code: merged.accountCode ?? null,
      label: merged.label ?? null,
      line_item_key: merged.lineItemKey ?? null,
      normalized_label: merged.normalizedLabel ?? null,
      contributions: merged.contributions ?? {},
    })),
    source_column: item.sourceColumn ?? null,
    source_page_or_cell: item.sourcePageOrCell ?? null,
    raw: {
      section: item.rawSection ?? null,
      label: item.label ?? item.name,
    },
    note: item.note ?? null,
  }));
}

export async function deleteActiveDraft(
  hoaId: number | string,
): Promise<{ deleted_draft_id: number }> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/draft`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  return handleResponse<{ deleted_draft_id: number }>(res);
}

export async function getActiveBudgetDraft(hoaId: number | string): Promise<BudgetDraftPayload> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/draft`, { headers: authHeaders() });
  return handleResponse<BudgetDraftPayload>(res);
}

export async function getBudgetDraft(
  hoaId: number | string,
  draftId: number | string,
): Promise<BudgetDraftPayload> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/drafts/${draftId}`, {
    headers: authHeaders(),
  });
  return handleResponse<BudgetDraftPayload>(res);
}

export async function downloadBudgetDraftEnriched(
  hoaId: number | string,
  draftId: number | string,
): Promise<Blob> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/drafts/${draftId}/download-enriched`, {
    headers: authHeaders(),
  });
  return handleBlobResponse(res);
}

export async function uploadBudgetSource(
  hoaId: number | string,
  file: File,
  sourceMode: BudgetSourceMode,
  assessmentMode: AssessmentMode,
): Promise<BudgetUploadResponse> {
  const formData = createBudgetSourceFormData(file, sourceMode, assessmentMode);
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/upload`, {
    method: 'POST',
    headers: authHeaders(),
    body: formData,
  });
  return handleResponse<BudgetUploadResponse>(res);
}

export async function uploadBudgetBundle(
  hoaId: number | string,
  budgetFile: File,
  reserveStudyFile: File,
  sourceMode: BudgetSourceMode,
  assessmentMode: AssessmentMode,
): Promise<BudgetBundleUploadResponse> {
  const formData = createBudgetBundleFormData(
    budgetFile,
    reserveStudyFile,
    sourceMode,
    assessmentMode,
  );
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/upload-bundle`, {
    method: 'POST',
    headers: authHeaders(),
    body: formData,
  });
  return handleResponse<BudgetBundleUploadResponse>(res);
}

export async function saveBudgetDraft(
  hoaId: number | string,
  payload: SaveBudgetDraftPayload,
): Promise<SaveBudgetDraftResponse> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/draft`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(payload),
  });
  return handleResponse<SaveBudgetDraftResponse>(res);
}

function ifMatchHeader(version: number | undefined): HeadersInit {
  return version != null ? { 'If-Match': String(version) } : {};
}

export async function listBudgetGlMerges(
  hoaId: number | string,
): Promise<BudgetGlMergeListItem[]> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/merges`, {
    headers: authHeaders(),
  });
  return handleResponse<BudgetGlMergeListItem[]>(res);
}

export async function fetchBudgetGlMergeSuggestions(
  hoaId: number | string,
): Promise<BudgetGlMergeSuggestionPayload[]> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/merges/suggest`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return handleResponse<BudgetGlMergeSuggestionPayload[]>(res);
}

export async function commitBudgetGlMerge(
  hoaId: number | string,
  payload: BudgetGlMergeCommitRequest,
  expectedVersion: number,
): Promise<BudgetGlMergeCommitResponse> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/merges`, {
    method: 'POST',
    headers: {
      ...authHeaders(),
      ...ifMatchHeader(expectedVersion),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  return handleResponse<BudgetGlMergeCommitResponse>(res);
}

export async function unmergeBudgetGlMergeApplication(
  hoaId: number | string,
  applicationId: number | string,
  expectedVersion: number,
): Promise<BudgetGlMergeCommitResponse> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/budget/merges/applications/${applicationId}/unmerge`,
    {
      method: 'POST',
      headers: {
        ...authHeaders(),
        ...ifMatchHeader(expectedVersion),
      },
    },
  );
  return handleResponse<BudgetGlMergeCommitResponse>(res);
}

export async function saveReserveStudyRows(
  hoaId: number | string,
  draftId: number | string,
  payload: SaveReserveStudyPayload,
): Promise<BudgetDraftPayload> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/drafts/${draftId}/reserve-study`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(payload),
  });
  return handleResponse<BudgetDraftPayload>(res);
}

export async function applyReserveStudyToBudget(
  hoaId: number | string,
  draftId: number | string,
): Promise<ApplyReserveStudyResponse> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/drafts/${draftId}/reserve-study/apply`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return handleResponse<ApplyReserveStudyResponse>(res);
}

export async function saveBudgetNote(
  hoaId: number | string,
  payload: SaveBudgetNotePayload,
): Promise<SaveBudgetNoteResponse> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/notes`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(payload),
  });
  return handleResponse<SaveBudgetNoteResponse>(res);
}

export async function generateBudgetVersion(
  hoaId: number | string,
  payload: GenerateBudgetVersionPayload,
): Promise<GenerateBudgetVersionResponse> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/generate`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(payload),
  });
  return handleResponse<GenerateBudgetVersionResponse>(res);
}

export async function getDraftCompareOptions(
  hoaId: number | string,
): Promise<BudgetDraftCompareOptionsResponse> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/compare/options`, {
    headers: authHeaders(),
  });
  return handleResponse<BudgetDraftCompareOptionsResponse>(res);
}

export async function compareDraftToBaseline(
  hoaId: number | string,
  draftId: number | string,
  payload: BudgetDraftCompareRequest,
): Promise<BudgetDraftCompareResponse> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/drafts/${draftId}/compare`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(payload),
  });
  return handleResponse<BudgetDraftCompareResponse>(res);
}

export async function getBudgetHistory(hoaId: number | string): Promise<BudgetHistoryResponse> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/history`, { headers: authHeaders() });
  return handleResponse<BudgetHistoryResponse>(res);
}

export async function getBudgetVersion(
  hoaId: number | string,
  versionId: number | string,
): Promise<BudgetVersionDetail> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/versions/${versionId}`, { headers: authHeaders() });
  return handleResponse<BudgetVersionDetail>(res);
}

export async function compareBudgetVersions(
  hoaId: number | string,
  leftVersionId: number | string,
  rightVersionId: number | string,
): Promise<BudgetVersionCompareResponse> {
  const res = await fetch(
    `${BASE_URL}/hoa/${hoaId}/versions/compare?left=${leftVersionId}&right=${rightVersionId}`,
    { headers: authHeaders() },
  );
  return handleResponse<BudgetVersionCompareResponse>(res);
}

export async function reopenBudgetVersion(
  hoaId: number | string,
  versionId: number | string,
): Promise<ReopenBudgetVersionResponse> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/versions/${versionId}/reopen`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return handleResponse<ReopenBudgetVersionResponse>(res);
}

export async function updateBudgetVersionMetadata(
  hoaId: number | string,
  versionId: number | string,
  payload: UpdateBudgetVersionMetadataPayload,
): Promise<UpdateBudgetVersionMetadataResponse> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/versions/${versionId}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(payload),
  });
  return handleResponse<UpdateBudgetVersionMetadataResponse>(res);
}

export async function downloadBudgetVersionFile(
  hoaId: number | string,
  versionId: number | string,
): Promise<Blob> {
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/versions/${versionId}/download`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return handleBlobResponse(res);
}
