export interface MergedBudgetLine {
  accountCode?: string | null;
  label?: string | null;
  lineItemKey?: string | null;
  normalizedLabel?: string | null;
  contributions?: {
    current_period?: number | null;
    ytd?: number | null;
    annual_budget?: number | null;
    projection?: number | null;
    variance?: number | null;
  } | null;
}

export interface LineItem {
  id: string;
  category: 'income' | 'operating' | 'reserve' | 'reserve_income' | 'reserve_expense';
  name: string;
  ytdActual: number;
  annualBudget: number;
  percentChange: number; // display %, user-editable
  projection?: number;   // backend col AL: YTD × growth factor
  currentPeriod?: number | null;
  variance?: number | null;
  readOnly?: boolean;    // true = excluded from board-adjustable flow (reserve-study rows)
  readOnlyOverride?: boolean | null; // explicit per-line unlock (null = use category default)
  accountCode?: number;  // 60000 — parsed from "60000 - Electricity & Gas"
  label?: string;        // "60000 - Electricity & Gas" — full string from col B
  lineItemKey?: string | null;
  normalizedLabel?: string | null;
  fundType?: string | null;
  mergedCount?: number;
  mergedGls?: MergedBudgetLine[];
  reserveGroup?: 'component' | 'income' | 'transfer' | null;
  rawSection?: string | null;
  sourceColumn?: string | null;
  sourcePageOrCell?: string | null;
  note?: {
    title: string;
    body: string;
  };
}

// HOA records are served by the backend API (/hoa) as HOARecord.

export interface AISuggestion {
  lineItemId: string;         // maps to LineItem.id (string form of feedback_case_id)
  lineItemName: string;
  currentPercent: number;     // derived from lineItems state (frontend-side)
  suggestedPercent: number;   // suggested_pct_change × 100
  confidence: number;         // confidence × 100
  reason: string;
  revisedByPass2?: boolean;
  cbrMatch?: number | null;
  mlBaseline?: number | null;
  feedbackCaseId?: number;    // backend feedback_case_id for submitting feedback
}

// Backend suggestion shape — matches actual FastAPI JSON response (snake_case)
export interface BackendSuggestion {
  id: number;                    // feedback_case_id
  account_code: number;
  account_name: string;
  suggested_pct_change: number;  // decimal, e.g. -0.094
  reason: string;
  confidence: number;            // 0.0–1.0
  revised_by_pass2: boolean;
  cbr_match: number | null;
  ml_baseline: number | null;
}

export interface AISuggestionResponse {
  run_id: number;
  suggestions: BackendSuggestion[];
  executive_summary: string;
  coherence_score: 'high' | 'medium' | 'low';
  total_budget_impact: string;
  flagged_items: FlaggedItem[];
  projected_deficit: number;
  recommended_assessment_increase_pct: number;
  assessment_recommendation_note: string;
}

export interface FlaggedItem {
  account_code: number;
  issue: string;
  revised_pct_change: number;
  revised_reason: string;
}

export interface FeedbackDecision {
  feedbackCaseId: number;
  decision: 'accepted' | 'modified' | 'rejected';
  finalPctChange: number;
  note?: string;
}

export interface FeedbackResponse {
  updated: number;
  total_cases: number;
}
