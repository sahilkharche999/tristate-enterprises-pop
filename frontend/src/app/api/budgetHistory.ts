import { BASE_URL } from './config';
import { authHeaders, handleBlobResponse, handleResponse } from './http';
import type { LineItem } from '../data/mockData';

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
  reopened_from_version_id?: number | null;
  line_items: JsonObject[];
  global_note?: string | null;
  statement_month?: number | null;
  growth_factor?: number | null;
  growth_factor_note?: string | null;
  reserve_inflation_rate: number;
  reserve_inflation_note?: string | null;
  updated_at?: string | null;
  upload_filename?: string | null;
  enriched_file_available?: boolean | null;
}

export interface BudgetDraftSummary {
  id: number;
  status: string;
  source_upload_id?: number | null;
  source_upload_filename?: string | null;
  reopened_from_version_id?: number | null;
  reopened_from_version_code?: string | null;
  reserve_inflation_rate: number;
  reserve_inflation_note?: string | null;
  updated_at?: string | null;
  actor_name: string;
  enriched_file_available?: boolean | null;
}

export interface BudgetHistoryResponse {
  active_draft?: BudgetDraftPayload | null;
  drafts: BudgetDraftSummary[];
  timeline: BudgetTimelineEvent[];
  versions: BudgetVersionSummary[];
  notes: BudgetNoteRecord[];
}

export interface BudgetUploadResponse {
  upload_id: number;
  draft: BudgetDraftPayload;
  timeline_event: BudgetTimelineEvent;
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
  if (explicit === 'income' || explicit === 'operating' || explicit === 'reserve') {
    return explicit;
  }

  const normalizedLabel = label.toLowerCase();
  if (normalizedLabel.includes('reserve')) {
    return 'reserve';
  }
  if (
    normalizedLabel.includes('income') ||
    normalizedLabel.includes('revenue') ||
    normalizedLabel.includes('assessment') ||
    normalizedLabel.includes('late fee') ||
    normalizedLabel.includes('interest')
  ) {
    return 'income';
  }
  if (accountCode != null && accountCode < 5000) {
    return 'income';
  }
  return 'operating';
}

export function budgetHistoryLineItemToEditorItem(record: JsonObject, index: number): LineItem {
  const raw = readRecord(record.raw);
  const accountCodeValue = record.account_code ?? raw['Account Code'] ?? raw.Account;
  const accountCode = accountCodeValue == null ? undefined : Number(accountCodeValue) || undefined;
  const label =
    pickString(record.label, record.name, raw.Label, raw['Account Name'], raw.Description) ??
    `Line Item ${index + 1}`;
  const annualBudget = toNumber(record.annualBudget ?? record.annual_budget ?? raw['Annual Budget']);
  const projection = toNumber(record.projection ?? raw.Projection);
  const ytdActual = toNumber(record.ytdActual ?? record.ytd_actual ?? raw['YTD Actual']);
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
    readOnly: Boolean(record.readOnly ?? record.read_only),
    accountCode,
    label,
    reserveGroup: pickString(record.reserve_group, record.reserveGroup) as
      | 'component'
      | 'income'
      | 'transfer'
      | undefined,
    rawSection: pickString(readRecord(record.raw).section, record.section),
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
    line_item_key: String(item.accountCode ?? item.label ?? item.name),
    account_code: item.accountCode ?? null,
    ytdActual: item.ytdActual,
    ytd_actual: item.ytdActual,
    annualBudget: item.annualBudget,
    annual_budget: item.annualBudget,
    percentChange: item.percentChange,
    percent_change: item.percentChange,
    projection: item.projection ?? null,
    readOnly: item.readOnly ?? false,
    read_only: item.readOnly ?? false,
    reserve_group: item.reserveGroup ?? null,
    section: item.rawSection ?? null,
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

export async function uploadBudgetSource(hoaId: number | string, file: File): Promise<BudgetUploadResponse> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${BASE_URL}/hoa/${hoaId}/budget/upload`, {
    method: 'POST',
    headers: authHeaders(),
    body: formData,
  });
  return handleResponse<BudgetUploadResponse>(res);
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
