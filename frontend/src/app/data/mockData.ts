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

export interface HOA {
  id: string;
  name: string;
  fiscalYear: string;
  status: 'Not Started' | 'In Progress' | 'Completed';
  units: number;
  taxId: string;
  fiscalYearStart: string;
  year: number;
  city: string;
}

// hoaList removed — HOA records are now served by the backend API (/hoa).

export const initialLineItems: LineItem[] = [
  // Income Section
  { id: 'inc-1', category: 'income', name: 'Regular Assessments', ytdActual: 285000, annualBudget: 360000, percentChange: 0 },
  { id: 'inc-2', category: 'income', name: 'Late Fees', ytdActual: 3200, annualBudget: 4800, percentChange: 0 },
  { id: 'inc-3', category: 'income', name: 'Interest Income', ytdActual: 1850, annualBudget: 2400, percentChange: 0 },
  { id: 'inc-4', category: 'income', name: 'Miscellaneous Income', ytdActual: 2100, annualBudget: 3000, percentChange: 0 },
  
  // Operating Expenses
  { id: 'op-1', category: 'operating', name: 'Insurance', ytdActual: 24500, annualBudget: 32000, percentChange: 0 },
  { id: 'op-2', category: 'operating', name: 'Landscaping', ytdActual: 18200, annualBudget: 24000, percentChange: 0 },
  { id: 'op-3', category: 'operating', name: 'Utilities – Electric', ytdActual: 15600, annualBudget: 21000, percentChange: 0 },
  { id: 'op-4', category: 'operating', name: 'Utilities – Water', ytdActual: 12800, annualBudget: 18000, percentChange: 0 },
  { id: 'op-5', category: 'operating', name: 'Utilities – Gas', ytdActual: 8400, annualBudget: 11500, percentChange: 0 },
  { id: 'op-6', category: 'operating', name: 'Management Fees', ytdActual: 21000, annualBudget: 28000, percentChange: 0 },
  { id: 'op-7', category: 'operating', name: 'Printing & Postage', ytdActual: 3200, annualBudget: 4500, percentChange: 0 },
  { id: 'op-8', category: 'operating', name: 'Repairs & Maintenance', ytdActual: 28500, annualBudget: 40000, percentChange: 0 },
  { id: 'op-9', category: 'operating', name: 'Janitorial', ytdActual: 16800, annualBudget: 22000, percentChange: 0 },
  { id: 'op-10', category: 'operating', name: 'Elevator Maintenance', ytdActual: 9200, annualBudget: 12500, percentChange: 0 },
  { id: 'op-11', category: 'operating', name: 'HVAC Service', ytdActual: 7800, annualBudget: 10500, percentChange: 0 },
  { id: 'op-12', category: 'operating', name: 'Legal & Accounting', ytdActual: 12500, annualBudget: 16000, percentChange: 0 },
  { id: 'op-13', category: 'operating', name: 'Taxes – Federal', ytdActual: 5600, annualBudget: 7500, percentChange: 0 },
  { id: 'op-14', category: 'operating', name: 'Taxes – State', ytdActual: 3200, annualBudget: 4200, percentChange: 0 },
  { id: 'op-15', category: 'operating', name: 'Licenses & Permits', ytdActual: 2400, annualBudget: 3200, percentChange: 0 },
  
  // Reserve Contributions
  { id: 'res-1', category: 'reserve', name: 'Roof Replacement Reserve', ytdActual: 18000, annualBudget: 24000, percentChange: 0 },
  { id: 'res-2', category: 'reserve', name: 'Elevator Reserve', ytdActual: 15000, annualBudget: 20000, percentChange: 0 },
  { id: 'res-3', category: 'reserve', name: 'Exterior Paint Reserve', ytdActual: 12000, annualBudget: 16000, percentChange: 0 },
  { id: 'res-4', category: 'reserve', name: 'Plumbing Reserve', ytdActual: 9000, annualBudget: 12000, percentChange: 0 },
  { id: 'res-5', category: 'reserve', name: 'Contingency Reserve', ytdActual: 7500, annualBudget: 10000, percentChange: 0 },
];

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
  catboost_active: boolean;
}

export interface AIStatsResponse {
  total_cases: number;
  catboost_active: boolean;
  last_training: string | null;
  properties: string[];
  sop_rules: number;
}

export interface KnowledgeBaseFolder {
  id: string;
  name: string;
  files: KnowledgeBaseFile[];
}

export interface KnowledgeBaseFile {
  id: string;
  name: string;
  year: string;
  status?: string;
}

// Knowledge Base is mock-only — will be replaced by backend API in Phase 5
export const getKnowledgeBaseFolders = (hoaId: string): KnowledgeBaseFolder[] => {
  const hoaName = 'HOA';
  
  return [
    {
      id: 'templates',
      name: 'HOA Templates',
      files: [
        { id: 'f1', name: 'Standard Budget Template.xlsx', year: '2025', status: 'Active' },
        { id: 'f2', name: 'Reserve Study Template.xlsx', year: '2025', status: 'Active' },
        { id: 'f3', name: 'Assessment Notice Template.docx', year: '2025', status: 'Active' },
      ],
    },
    {
      id: 'previous',
      name: 'Previous Years',
      files: [
        { id: 'f4', name: `${hoaName} - 2024 Final Budget.xlsx`, year: '2024', status: 'Archived' },
        { id: 'f5', name: `${hoaName} - 2023 Final Budget.xlsx`, year: '2023', status: 'Archived' },
        { id: 'f6', name: `${hoaName} - 2022 Final Budget.xlsx`, year: '2022', status: 'Archived' },
      ],
    },
    {
      id: 'approved',
      name: 'Approved Budgets',
      files: [
        { id: 'f7', name: `${hoaName} - 2025 Approved Budget.pdf`, year: '2025', status: 'Approved' },
        { id: 'f8', name: 'Board Resolution 2025-01.pdf', year: '2025', status: 'Approved' },
      ],
    },
    {
      id: 'rejected',
      name: 'Rejected Budgets',
      files: [
        { id: 'f9', name: `${hoaName} - 2025 Draft v1.xlsx`, year: '2025', status: 'Rejected' },
      ],
    },
    {
      id: 'board',
      name: 'Board Notes',
      files: [
        { id: 'f10', name: 'Budget Meeting Minutes Jan 2025.pdf', year: '2025', status: 'Final' },
        { id: 'f11', name: 'Budget Discussion Notes.docx', year: '2025', status: 'Draft' },
      ],
    },
    {
      id: 'audit',
      name: 'Audit Feedback',
      files: [
        { id: 'f12', name: '2024 Audit Report.pdf', year: '2024', status: 'Final' },
        { id: 'f13', name: 'Auditor Recommendations.docx', year: '2024', status: 'Final' },
      ],
    },
    {
      id: 'compliance',
      name: 'Compliance Documents',
      files: [
        { id: 'f14', name: 'HOA Governing Documents.pdf', year: '2025', status: 'Current' },
        { id: 'f15', name: 'State Compliance Checklist.pdf', year: '2025', status: 'Current' },
      ],
    },
    {
      id: 'reserve',
      name: 'Reserve Studies',
      files: [
        { id: 'f16', name: `${hoaName} Reserve Study 2024.pdf`, year: '2024', status: 'Current' },
        { id: 'f17', name: 'Capital Expenditure Plan.xlsx', year: '2025', status: 'Draft' },
      ],
    },
  ];
};
