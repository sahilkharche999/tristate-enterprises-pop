/**
 * Honest budget entry CTAs: never claim "Open Current Draft" without an active draft.
 */

export type BudgetEntryMode =
  | 'active_draft'
  | 'latest_generated'
  | 'create_draft';

export interface BudgetEntryInput {
  hoaId: string | number;
  hasActiveDraft: boolean;
  latestVersionId?: number | null;
  /** Optional version code for helper copy */
  latestVersionCode?: string | null;
}

export interface BudgetEntryCta {
  mode: BudgetEntryMode;
  /** Primary button label */
  label: string;
  /** Primary navigation href */
  href: string;
  /** Short description under/near the CTA */
  description: string;
  /** Optional secondary CTA (e.g. create new draft when a version exists) */
  secondaryLabel?: string;
  secondaryHref?: string;
}

/**
 * Map draft/version presence to the truthful primary budget entry action.
 */
export function resolveBudgetEntryCta(input: BudgetEntryInput): BudgetEntryCta {
  const hoaId = String(input.hoaId);
  const latestId =
    input.latestVersionId != null && Number.isFinite(Number(input.latestVersionId))
      ? Number(input.latestVersionId)
      : null;

  if (input.hasActiveDraft) {
    return {
      mode: 'active_draft',
      label: 'Open Current Draft',
      href: `/hoa/${hoaId}`,
      description: 'Jump back into the active budget draft.',
    };
  }

  if (latestId != null) {
    const code = input.latestVersionCode?.trim();
    return {
      mode: 'latest_generated',
      label: code ? `Open latest generated (${code})` : 'Open Latest Generated',
      href: `/hoa/${hoaId}?generated=true&versionId=${latestId}&readOnly=1`,
      description:
        'No active draft — open the latest generated budget version, or create a new draft.',
      secondaryLabel: 'Create new budget draft',
      secondaryHref: `/hoa/${hoaId}?create=1`,
    };
  }

  return {
    mode: 'create_draft',
    label: 'Create Budget Draft',
    href: `/hoa/${hoaId}`,
    description: 'No active draft or generated version yet. Upload a budget source to begin.',
  };
}

/** True when the UI may show the literal “Open Current Draft” label. */
export function canShowOpenCurrentDraft(hasActiveDraft: boolean): boolean {
  return hasActiveDraft === true;
}
