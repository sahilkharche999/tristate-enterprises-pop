// Color helper for the disclosure-package job-stage chip strip.
// UI-SPEC §6.2 designates this as a sibling of statusColors.ts (which keys off
// HOA workflow status, not job status). UI-SPEC §7.3 fixes the three chip
// states (done / active / pending) and their hex values.

export type StageChipState = 'done' | 'active' | 'pending';

export interface StageColors {
  bg: string;
  text: string;
  border: string;
}

export function getJobStageColor(state: StageChipState): StageColors {
  switch (state) {
    case 'done':
      return { bg: '#d1fae5', text: '#065f46', border: '#a7f3d0' };
    case 'active':
      return { bg: '#dbeafe', text: '#1e40af', border: '#bfdbfe' };
    case 'pending':
    default:
      return { bg: '#f5f5f5', text: '#737373', border: '#e5e5e5' };
  }
}

// Canonical ordering. Backend stage names in `DisclosurePackageJob.stage` use
// the first five tokens; we append `completed` so a job that finished on the
// last `verifying` poll renders all chips as done.
export const STAGE_ORDER: Array<
  'validating' | 'computing' | 'rendering' | 'merging' | 'verifying' | 'completed'
> = ['validating', 'computing', 'rendering', 'merging', 'verifying', 'completed'];

// Pill labels (UI-SPEC §9.4: "Validating, Computing, Rendering, Merging, Ready").
// "Ready" is the user-facing label for the final stages.
export const STAGE_LABEL: Record<string, string> = {
  validating: 'Validating',
  computing: 'Computing',
  rendering: 'Rendering',
  merging: 'Merging',
  verifying: 'Ready',
  completed: 'Ready',
};
