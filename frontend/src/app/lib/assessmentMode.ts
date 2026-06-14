export type AssessmentMode = 'fixed' | 'variable';

export interface AssessmentModeOption {
  value: AssessmentMode;
  label: string;
  description: string;
}

export const ASSESSMENT_MODE_OPTIONS: AssessmentModeOption[] = [
  {
    value: 'fixed',
    label: 'Fixed / Uniform Per Unit',
    description:
      'Every unit pays the same regular dues amount. DRE upload and mapping review are not required for the regular schedule.',
  },
  {
    value: 'variable',
    label: 'Mixed / Variable',
    description:
      'Different unit groups or units can pay different dues. Keep the DRE-backed setup and mapping review path active.',
  },
];

export function assessmentModeLabel(mode: AssessmentMode | null | undefined): string {
  return mode === 'fixed' ? 'Fixed / Uniform Per Unit' : 'Mixed / Variable';
}

export function assessmentModeShortLabel(mode: AssessmentMode | null | undefined): string {
  return mode === 'fixed' ? 'Fixed' : 'Variable';
}

export function assessmentModeHelperCopy(mode: AssessmentMode): string {
  if (mode === 'fixed') {
    return 'Choose this when every unit pays the same dues amount each month. This is separate from the budget document source mode.';
  }
  return 'Choose this when the HOA needs a DRE-backed setup because different groups or units can owe different regular dues.';
}

export function assessmentModeWorkflowCopy(mode: AssessmentMode): string {
  if (mode === 'fixed') {
    return 'Fixed mode skips DRE upload, DRE review, and annual budget-to-pool mapping review for regular dues.';
  }
  return 'Variable mode requires an approved DRE setup and current-year mapping review before final rendering.';
}
