import { BASE_URL } from './config';
import { authHeaders, handleResponse } from './http';

// ─── Shared types (Excel table shape used by budget screens) ─────────────────

export type CellValue = string | number | boolean | null;

export interface SheetTable {
  sheet: string;
  headers: CellValue[];
  rows: CellValue[][];
}

// ─── Utilities ───────────────────────────────────────────────────────────────

/** Coerce a cell value from an Excel response to a number. */
export function toNum(v: CellValue): number {
  return typeof v === 'number' ? v : parseFloat(String(v ?? 0)) || 0;
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

export async function getLatestAISuggestions(
  propertyId: string
): Promise<import('../data/mockData').AISuggestionResponse | null> {
  const res = await fetch(`${BASE_URL}/ai/suggest/${propertyId}/latest`, {
    headers: authHeaders(),
  });
  if (res.status === 404) return null;
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

export async function exportData(): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE_URL}/ai/export`, { headers: authHeaders() });
  return handleResponse<Record<string, unknown>>(res);
}
