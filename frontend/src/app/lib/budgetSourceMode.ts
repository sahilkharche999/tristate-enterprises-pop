import type { AssessmentMode } from './assessmentMode';

export type BudgetSourceMode = 'income_statement' | 'proforma_final_budget';

export interface BudgetSourceModeOption {
  value: BudgetSourceMode;
  label: string;
}

// "Read as" overrides. Gemini detects the kind of file unless the operator picks one.
export const BUDGET_SOURCE_MODE_OPTIONS: BudgetSourceModeOption[] = [
  { value: 'income_statement', label: 'Income statement' },
  { value: 'proforma_final_budget', label: 'Final budget' },
];

export function budgetSourceModeLabel(mode: BudgetSourceMode | null | undefined): string {
  return mode === 'proforma_final_budget' ? 'Pro Forma / Final Budget' : 'Income Statement';
}

export function budgetSourceModeShortLabel(mode: BudgetSourceMode | null | undefined): string {
  return mode === 'proforma_final_budget' ? 'Pro Forma' : 'Income Statement';
}

export function budgetSourceModeCreateSuccess(mode: BudgetSourceMode | null | undefined): string {
  return mode === 'proforma_final_budget'
    ? 'Draft created from a final budget.'
    : 'Draft created from an income statement.';
}

export function budgetSourceModeGenericDraftError(): string {
  return 'Upload a budget source first.';
}

// `sourceMode` is only sent as an override; leave it null to let Gemini detect the kind.
export function createBudgetSourceFormData(
  file: File,
  sourceMode?: BudgetSourceMode | null,
  assessmentMode?: AssessmentMode,
): FormData {
  const formData = new FormData();
  if (sourceMode) {
    formData.append('source_mode', sourceMode);
  }
  if (assessmentMode) {
    formData.append('assessment_mode', assessmentMode);
  }
  formData.append('file', file);
  return formData;
}

export function createBudgetBundleFormData(
  budgetFile: File,
  reserveStudyFile: File,
  sourceMode?: BudgetSourceMode | null,
  assessmentMode?: AssessmentMode,
): FormData {
  const formData = new FormData();
  if (sourceMode) {
    formData.append('source_mode', sourceMode);
  }
  if (assessmentMode) {
    formData.append('assessment_mode', assessmentMode);
  }
  formData.append('budget_file', budgetFile);
  formData.append('reserve_study_file', reserveStudyFile);
  return formData;
}
