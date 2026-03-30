import { BASE_URL } from './config';
import { authHeaders, handleBlobResponse, handleResponse } from './http';

// ─── Response Types ───────────────────────────────────────────────────────────

export type CellValue = string | number | boolean | null;

export interface SheetTable {
  sheet: string;
  headers: CellValue[];
  rows: CellValue[][];
}

export interface GenerateBudgetResponse {
  growth_factor: number;
  growth_factor_note: string;
  statement_month?: number;
  enriched: SheetTable;
  budget_preview: SheetTable;
}

export interface TableResponse {
  sheet: string;
  headers: CellValue[];
  rows: CellValue[][];
}

export interface SumResponse {
  sheet: string;
  sum_cell: string;
  sum: number;
}

export interface DupsResponse {
  duplicates: { row: number; value: CellValue }[];
}

export interface SimpleResponse {
  message: string;
}

export interface RunPipelineResponse {
  [key: string]: unknown;
  note?: string;
}

export interface CumulativeSumResponse {
  cumulative_sum: number;
  start_row: number;
  end_row: number;
}

// ─── Utilities ───────────────────────────────────────────────────────────────

/** Coerce a cell value from an Excel response to a number. */
export function toNum(v: CellValue): number {
  return typeof v === 'number' ? v : parseFloat(String(v ?? 0)) || 0;
}

// ─── API Functions ────────────────────────────────────────────────────────────

export async function generateBudget(params: {
  file: File;
  enrichOnly: boolean;
  percentChanges?: Record<string, number>;
  fiscalYearStartMonth?: number;
  growthFactor?: number;
}): Promise<GenerateBudgetResponse> {
  const fd = new FormData();
  fd.append('file', params.file);
  fd.append('enrich_only', String(params.enrichOnly));
  if (params.percentChanges) fd.append('percent_changes_json', JSON.stringify(params.percentChanges));
  if (params.fiscalYearStartMonth != null) fd.append('fiscal_year_start_month', String(params.fiscalYearStartMonth));
  if (params.growthFactor != null) fd.append('growth_factor', String(params.growthFactor));
  const res = await fetch(`${BASE_URL}/macros/generate-budget`, { method: 'POST', body: fd, headers: authHeaders() });
  return handleResponse<GenerateBudgetResponse>(res);
}

export async function removeProtection(params: { file: File }): Promise<Blob> {
  const fd = new FormData();
  fd.append('file', params.file);
  const res = await fetch(`${BASE_URL}/macros/remove-protection`, { method: 'POST', body: fd, headers: authHeaders() });
  return handleBlobResponse(res);
}

export async function runPipeline(params: { file: File; sheet?: string }): Promise<RunPipelineResponse> {
  const fd = new FormData();
  fd.append('file', params.file);
  if (params.sheet) fd.append('sheet', params.sheet);
  const res = await fetch(`${BASE_URL}/macros/run-pipeline`, { method: 'POST', body: fd, headers: authHeaders() });
  return handleResponse<RunPipelineResponse>(res);
}

export async function paymentSearch(params: { file: File; sheet?: string }): Promise<TableResponse> {
  const fd = new FormData();
  fd.append('file', params.file);
  if (params.sheet) fd.append('sheet', params.sheet);
  const res = await fetch(`${BASE_URL}/macros/payment-search`, { method: 'POST', body: fd, headers: authHeaders() });
  return handleResponse<TableResponse>(res);
}

export async function sumAndFormat(params: {
  file: File;
  sheet: string;
  startCell: string;
  rowCount: number;
  targetCol?: string;
}): Promise<SumResponse> {
  const fd = new FormData();
  fd.append('file', params.file);
  fd.append('sheet', params.sheet);
  fd.append('start_cell', params.startCell);
  fd.append('row_count', String(params.rowCount));
  if (params.targetCol) fd.append('target_col', params.targetCol);
  const res = await fetch(`${BASE_URL}/macros/sum-and-format`, { method: 'POST', body: fd, headers: authHeaders() });
  return handleResponse<SumResponse>(res);
}

export async function achSum(params: {
  file: File;
  sheet: string;
  startCell: string;
  findText?: string;
}): Promise<SumResponse> {
  const fd = new FormData();
  fd.append('file', params.file);
  fd.append('sheet', params.sheet);
  fd.append('start_cell', params.startCell);
  if (params.findText) fd.append('find_text', params.findText);
  const res = await fetch(`${BASE_URL}/macros/ach-sum`, { method: 'POST', body: fd, headers: authHeaders() });
  return handleResponse<SumResponse>(res);
}

export async function oneCell(params: { file: File; sheet: string; startCell: string }): Promise<SimpleResponse> {
  const fd = new FormData();
  fd.append('file', params.file);
  fd.append('sheet', params.sheet);
  fd.append('start_cell', params.startCell);
  const res = await fetch(`${BASE_URL}/macros/one-cell`, { method: 'POST', body: fd, headers: authHeaders() });
  return handleResponse<SimpleResponse>(res);
}

export async function findDups(params: {
  file: File;
  sheet: string;
  lookupCol?: string;
}): Promise<DupsResponse> {
  const fd = new FormData();
  fd.append('file', params.file);
  fd.append('sheet', params.sheet);
  if (params.lookupCol) fd.append('lookup_col', params.lookupCol);
  const res = await fetch(`${BASE_URL}/macros/find-dups`, { method: 'POST', body: fd, headers: authHeaders() });
  return handleResponse<DupsResponse>(res);
}

export async function cumulativeSum(params: {
  file: File;
  sheet: string;
  startRow: number;
  startCol: number;
  endRow: number;
}): Promise<CumulativeSumResponse> {
  const fd = new FormData();
  fd.append('file', params.file);
  fd.append('sheet', params.sheet);
  fd.append('start_row', String(params.startRow));
  fd.append('start_col', String(params.startCol));
  fd.append('end_row', String(params.endRow));
  const res = await fetch(`${BASE_URL}/macros/cumulative-sum`, { method: 'POST', body: fd, headers: authHeaders() });
  return handleResponse<CumulativeSumResponse>(res);
}

// ─── AI Budget Pipeline API ───────────────────────────────────────────────────

export interface AILineItemInput {
  account_code: number;
  account_name: string;
  label: string;
  category?: string;
  ytd_actual: number;
  annual_budget: number;
  projection?: number;
  current_pct_change?: number;
}

export async function getAISuggestions(params: {
  lineItems: import('../data/mockData').LineItem[];
  propertyName: string;
  totalAnnualBudget: number;
  totalYtdActuals: number;
  pctYearElapsed: number;
  fiscalYear: number;
  statementMonth: number;
  growthFactor: number;
}): Promise<import('../data/mockData').AISuggestionResponse> {
  // Filter out readOnly items and items without accountCode
  const activeItems: AILineItemInput[] = params.lineItems
    .filter((item) => !item.readOnly && item.accountCode != null)
    .map((item) => ({
      account_code: item.accountCode!,
      account_name: item.name,
      label: item.label || `${item.accountCode} - ${item.name}`,
      category: item.category,
      ytd_actual: item.ytdActual,
      annual_budget: item.annualBudget,
      projection: item.projection,
      current_pct_change: item.percentChange / 100,
    }));

  const body = {
    property_name: params.propertyName,
    line_items: activeItems,
    total_annual_budget: params.totalAnnualBudget,
    total_ytd_actuals: params.totalYtdActuals,
    pct_year_elapsed: params.pctYearElapsed,
    fiscal_year: params.fiscalYear,
    statement_month: params.statementMonth,
    growth_factor: params.growthFactor,
  };

  const res = await fetch(`${BASE_URL}/ai/suggest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  return handleResponse<import('../data/mockData').AISuggestionResponse>(res);
}

export async function submitAIFeedback(params: {
  runId: number;
  decisions: import('../data/mockData').FeedbackDecision[];
}): Promise<import('../data/mockData').FeedbackResponse> {
  const body = {
    run_id: params.runId,
    decisions: params.decisions.map((d) => ({
      feedback_case_id: d.feedbackCaseId,
      decision: d.decision,
      final_pct_change: d.finalPctChange,
      note: d.note || '',
    })),
  };
  const res = await fetch(`${BASE_URL}/ai/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  return handleResponse<import('../data/mockData').FeedbackResponse>(res);
}

export async function getAIStats(): Promise<import('../data/mockData').AIStatsResponse> {
  const res = await fetch(`${BASE_URL}/ai/stats`, { headers: authHeaders() });
  return handleResponse<import('../data/mockData').AIStatsResponse>(res);
}

export async function exportData(): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE_URL}/ai/export`, { headers: authHeaders() });
  return handleResponse<Record<string, unknown>>(res);
}
