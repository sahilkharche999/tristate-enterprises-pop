import type { AssessmentMode } from './assessmentMode';

export type BudgetSourceMode = 'income_statement' | 'proforma_final_budget';

export interface BudgetSourceModeOption {
  value: BudgetSourceMode;
  label: string;
  description: string;
}

export const BUDGET_SOURCE_MODE_OPTIONS: BudgetSourceModeOption[] = [
  {
    value: 'income_statement',
    label: 'Income Statement',
    description: 'Use the current income-statement parser for accountant-style Excel and PDF statements.',
  },
  {
    value: 'proforma_final_budget',
    label: 'Pro Forma / Final Budget',
    description: 'Use the pro-forma parser for exported operating/cash-flow budget sheets with final annual amounts.',
  },
];

export function budgetSourceModeLabel(mode: BudgetSourceMode | null | undefined): string {
  return mode === 'proforma_final_budget' ? 'Pro Forma / Final Budget' : 'Income Statement';
}

export function budgetSourceModeShortLabel(mode: BudgetSourceMode | null | undefined): string {
  return mode === 'proforma_final_budget' ? 'Pro Forma' : 'Income Statement';
}

export function budgetSourceModeHelperCopy(mode: BudgetSourceMode): string {
  if (mode === 'proforma_final_budget') {
    return 'Upload an exported operating/cash-flow budget with Jan-Dec columns and a final, proposed, or annual budget column.';
  }
  return 'Upload an income statement or revenues-and-expenses statement with annual budget figures for each line item.';
}

export function budgetSourceModeUploadTitle(mode: BudgetSourceMode): string {
  return mode === 'proforma_final_budget' ? 'Pro Forma / Final Budget File' : 'Income Statement File';
}

export function budgetSourceModeUploadPlaceholder(mode: BudgetSourceMode): string {
  return mode === 'proforma_final_budget'
    ? 'Excel workbook or PDF pro forma / final budget'
    : 'Excel workbook or PDF income statement';
}

export function budgetSourceModeCreateSuccess(mode: BudgetSourceMode): string {
  return mode === 'proforma_final_budget'
    ? 'Pro forma / final budget uploaded and draft created.'
    : 'Income statement uploaded and draft created.';
}

export function budgetSourceModeMissingUploadError(mode: BudgetSourceMode): string {
  return mode === 'proforma_final_budget'
    ? 'Upload a pro forma / final budget first.'
    : 'Upload an income statement first.';
}

export function budgetSourceModeGenericDraftError(): string {
  return 'Upload a budget source first.';
}

export function createBudgetSourceFormData(
  file: File,
  sourceMode: BudgetSourceMode,
  assessmentMode?: AssessmentMode,
): FormData {
  const formData = new FormData();
  formData.append('source_mode', sourceMode);
  if (assessmentMode) {
    formData.append('assessment_mode', assessmentMode);
  }
  formData.append('file', file);
  return formData;
}

export function createBudgetBundleFormData(
  budgetFile: File,
  reserveStudyFile: File,
  sourceMode: BudgetSourceMode,
  assessmentMode?: AssessmentMode,
): FormData {
  const formData = new FormData();
  formData.append('source_mode', sourceMode);
  if (assessmentMode) {
    formData.append('assessment_mode', assessmentMode);
  }
  formData.append('budget_file', budgetFile);
  formData.append('reserve_study_file', reserveStudyFile);
  return formData;
}
