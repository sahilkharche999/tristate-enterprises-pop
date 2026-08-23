import type {
  CCRPoolCorrectionOperation,
  CCRPromotionIssue,
  CCRPromotionPreview,
  CCRRecommendedOperation,
  CCRUnitFactorEntry,
} from '../api/ccr.ts';
import { parseFriendlyCCRApiError } from '../api/ccr.ts';

type Extraction = Record<string, unknown>;
type AllocationCategory = Record<string, unknown>;

export type CCRFactorDraft = {
  unit_number: string;
  square_feet: string;
  ownership_percent: string;
  fixed_amounts?: Record<string, string>;
  custom_factors?: Record<string, string>;
};

export type CCRAdvancedCategoryDraft = {
  name: string;
  includedExpenses: string;
  billing: 'regular' | 'separate';
  cadence: 'recurring' | 'one_time';
  amountAvailability: 'known' | 'external_schedule' | 'operator_pending';
  amount: string;
  recipientScope:
    | 'all_units'
    | 'residential_only'
    | 'commercial_only'
    | 'parking_users'
    | 'custom_unit_list';
  participantUnitNumbers: string[];
  allocation:
    | 'equal'
    | 'square_footage'
    | 'ownership_percentage'
    | 'fixed_amount'
    | 'external_schedule';
  sourcePages: string;
};

export function generateCategoryKey(
  name: string,
  existingKeys: ReadonlySet<string>,
): string {
  const base =
    name
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 48) || 'charge';
  if (!existingKeys.has(base)) return base;
  let suffix = 2;
  while (existingKeys.has(`${base}-${suffix}`)) suffix += 1;
  return `${base}-${suffix}`;
}

function draftPages(value: string): number[] {
  return value
    .split(/[\s,]+/)
    .filter(Boolean)
    .map(Number)
    .filter((page) => Number.isInteger(page) && page > 0);
}

export function buildAdvancedCategoryPool(
  draft: CCRAdvancedCategoryDraft,
  categoryKey: string,
): Record<string, unknown> {
  const expenses = draft.includedExpenses
    .split(/[,\n]+/)
    .map((value) => value.trim())
    .filter(Boolean);
  const separate = draft.billing === 'separate';
  const allocationMethod =
    draft.allocation === 'fixed_amount'
      ? 'specified_value'
      : draft.allocation === 'external_schedule'
        ? 'custom_factor'
        : draft.allocation;
  const billingTreatment =
    draft.amountAvailability === 'operator_pending'
      ? 'operator_amount_pending'
      : draft.cadence === 'one_time'
        ? 'separate_one_time'
        : 'recurring';
  return {
    pool_key: categoryKey,
    parent_pool_key: '',
    pool_name: draft.name.trim(),
    annual_amount:
      draft.amountAvailability === 'known' && draft.amount.trim()
        ? draft.amount.trim()
        : null,
    monthly_amount: null,
    allocation_method: allocationMethod,
    recipient_scope: draft.recipientScope,
    selected_unit_numbers:
      draft.recipientScope !== 'all_units'
        ? draft.participantUnitNumbers
        : [],
    denominator_label: '',
    denominator_value: null,
    denominator_source: 'unknown',
    included_budget_lines: expenses,
    excluded_budget_lines: [],
    budget_line_derivation: expenses.length > 0 ? 'explicit_lines' : 'unknown',
    residual_after_pool_keys: [],
    residual_exclusions: [],
    source_pages: draftPages(draft.sourcePages),
    confidence: 1,
    allocation_context:
      separate && draft.cadence === 'one_time'
        ? 'special_assessment'
        : 'regular_operating',
    billing_treatment: billingTreatment,
    billing_cadence: draft.cadence,
    amount_availability: draft.amountAvailability,
    variable_flag: false,
    pool_kind: separate ? 'separately_billed_special_assessment' : '',
  };
}

export type CCRAdvancedFactorRequirements = {
  squareFeet: boolean;
  ownershipPercent: boolean;
  squareFeetUnitNumbers?: string[];
  ownershipPercentUnitNumbers?: string[];
  fixedCategoryKeys: string[];
  fixedRecipientUnitNumbers?: Record<string, string[]>;
  customCategoryKeys?: string[];
  customRecipientUnitNumbers?: Record<string, string[]>;
};

export function buildAdvancedFactorPayload(
  drafts: CCRFactorDraft[],
  expectedUnitCount: number | null,
  requirements: CCRAdvancedFactorRequirements,
): { values: CCRUnitFactorEntry[]; error: string | null } {
  if (
    expectedUnitCount != null &&
    expectedUnitCount > 0 &&
    drafts.length !== expectedUnitCount
  ) {
    return {
      values: [],
      error: `Enter all ${expectedUnitCount} homes before saving.`,
    };
  }
  if (drafts.length === 0 || drafts.some((row) => !row.unit_number.trim())) {
    return {
      values: [],
      error: 'Enter a home identifier on every row before saving.',
    };
  }
  const identifiers = drafts.map((row) => row.unit_number.trim().toLowerCase());
  if (new Set(identifiers).size !== identifiers.length) {
    return {
      values: [],
      error: 'Use a different home identifier on every row.',
    };
  }
  const values: CCRUnitFactorEntry[] = [];
  for (const row of drafts) {
    const entry: CCRUnitFactorEntry = { unit_number: row.unit_number.trim() };
    for (const [required, field, label] of [
      [requirements.squareFeet, 'square_feet', 'square feet'],
      [
        requirements.ownershipPercent,
        'ownership_percent',
        'ownership percentage',
      ],
    ] as const) {
      if (!required) continue;
      const eligibleUnits =
        field === 'square_feet'
          ? requirements.squareFeetUnitNumbers
          : requirements.ownershipPercentUnitNumbers;
      if (
        eligibleUnits &&
        !eligibleUnits.includes(row.unit_number.trim())
      ) {
        continue;
      }
      const numeric = Number(row[field].trim());
      if (!row[field].trim() || !Number.isFinite(numeric) || numeric <= 0) {
        return {
          values: [],
          error: `Enter a positive ${label} value for every home.`,
        };
      }
      entry[field] = numeric;
    }
    if (requirements.fixedCategoryKeys.length > 0) {
      const fixedAmounts: Record<string, number> = {};
      for (const categoryKey of requirements.fixedCategoryKeys) {
        const eligibleUnits =
          requirements.fixedRecipientUnitNumbers?.[categoryKey];
        if (
          eligibleUnits &&
          !eligibleUnits.includes(row.unit_number.trim())
        ) {
          continue;
        }
        const raw = row.fixed_amounts?.[categoryKey]?.trim() || '';
        const numeric = Number(raw);
        if (!raw || !Number.isFinite(numeric) || numeric <= 0) {
          return {
            values: [],
            error: 'Enter a positive fixed annual amount for every home.',
          };
        }
        fixedAmounts[categoryKey] = numeric;
      }
      if (Object.keys(fixedAmounts).length > 0) {
        entry.fixed_amounts = fixedAmounts;
      }
    }
    if ((requirements.customCategoryKeys || []).length > 0) {
      const customFactors: Record<string, number> = {};
      for (const categoryKey of requirements.customCategoryKeys || []) {
        const eligibleUnits =
          requirements.customRecipientUnitNumbers?.[categoryKey];
        if (
          eligibleUnits &&
          !eligibleUnits.includes(row.unit_number.trim())
        ) {
          continue;
        }
        const raw = row.custom_factors?.[categoryKey]?.trim() || '';
        const numeric = Number(raw);
        if (!raw || !Number.isFinite(numeric) || numeric <= 0) {
          return {
            values: [],
            error: 'Enter a positive custom factor for every participating home.',
          };
        }
        customFactors[categoryKey] = numeric;
      }
      if (Object.keys(customFactors).length > 0) {
        entry.custom_factors = customFactors;
      }
    }
    values.push(entry);
  }
  return { values, error: null };
}

export type CCRCorrectionResult =
  | { status: 'refreshed'; preview: CCRPromotionPreview }
  | { status: 'saved_refresh_failed'; refreshError: unknown };

export type CCRCorrectionAction =
  | { kind: 'operation'; value: CCRPoolCorrectionOperation }
  | { kind: 'scalar'; fieldPath: string; oldValue?: unknown; value: unknown }
  | { kind: 'factors'; values: CCRUnitFactorEntry[] };

export interface CCRCorrectionDependencies {
  saveOperation: (operation: CCRPoolCorrectionOperation) => Promise<unknown>;
  saveScalar: (correction: {
    field_path: string;
    old_value?: unknown;
    new_value: unknown;
  }) => Promise<unknown>;
  saveFactors: (factors: CCRUnitFactorEntry[]) => Promise<unknown>;
  refetchPreview: () => Promise<CCRPromotionPreview>;
}

export interface CCRIssueCardView {
  heading: string;
  whatHappened: string;
  ownerImpact: string;
  recommendation: string;
  evidence: Array<{ page: number; label: string }>;
}

export function ccrIssueIdentity(
  issue: Pick<CCRPromotionIssue, 'code' | 'category_key' | 'source_pages'> &
    Partial<Pick<CCRPromotionIssue, 'explanation'>>,
): string {
  const category = String(issue.category_key || '').trim();
  if (category) return `${issue.code}:${category}`;
  const stableSource = `${issue.code}|${[...issue.source_pages].sort((a, b) => a - b).join(',')}|${issue.explanation || ''}`;
  let hash = 5381;
  for (const character of stableSource) {
    hash = ((hash << 5) + hash) ^ character.charCodeAt(0);
  }
  return `${issue.code}:issue-${(hash >>> 0).toString(36)}`;
}

export function buildCCRFactorPayload(
  drafts: CCRFactorDraft[],
  expectedUnitCount: number | null,
  requiredField: 'square_feet' | 'ownership_percent',
): { values: CCRUnitFactorEntry[]; error: string | null } {
  if (
    expectedUnitCount != null &&
    expectedUnitCount > 0 &&
    drafts.length !== expectedUnitCount
  ) {
    return {
      values: [],
      error: `Enter all ${expectedUnitCount} homes before saving.`,
    };
  }
  if (drafts.length === 0 || drafts.some((row) => !row.unit_number.trim())) {
    return {
      values: [],
      error: 'Enter a home identifier on every row before saving.',
    };
  }
  const identifiers = drafts.map((row) => row.unit_number.trim().toLocaleLowerCase());
  if (new Set(identifiers).size !== identifiers.length) {
    return {
      values: [],
      error: 'Use a different home identifier on every row.',
    };
  }

  const values: CCRUnitFactorEntry[] = [];
  for (const row of drafts) {
    const requiredRaw = row[requiredField].trim();
    const requiredValue = Number(requiredRaw);
    if (
      !requiredRaw ||
      !Number.isFinite(requiredValue) ||
      requiredValue <= 0
    ) {
      return {
        values: [],
        error: 'Enter a positive number for every required home value.',
      };
    }
    const entry: CCRUnitFactorEntry = {
      unit_number: row.unit_number.trim(),
    };
    for (const field of ['square_feet', 'ownership_percent'] as const) {
      const raw = row[field].trim();
      if (!raw) continue;
      const numeric = Number(raw);
      if (!Number.isFinite(numeric) || numeric <= 0) {
        return {
          values: [],
          error: 'Enter a positive number for every home value.',
        };
      }
      entry[field] = numeric;
    }
    if (row.fixed_amounts && Object.keys(row.fixed_amounts).length > 0) {
      const fixedAmounts: Record<string, number> = {};
      for (const [categoryKey, raw] of Object.entries(row.fixed_amounts)) {
        const numeric = Number(raw.trim());
        if (!raw.trim() || !Number.isFinite(numeric) || numeric <= 0) {
          return {
            values: [],
            error: 'Enter a positive fixed annual amount for every home.',
          };
        }
        fixedAmounts[categoryKey] = numeric;
      }
      entry.fixed_amounts = fixedAmounts;
    }
    values.push(entry);
  }
  return { values, error: null };
}

function categories(extraction: Extraction | null): AllocationCategory[] {
  const value = extraction?.allocation_pools;
  return Array.isArray(value) ? (value as AllocationCategory[]) : [];
}

function categoryForIssue(
  issue: CCRPromotionIssue,
  extraction: Extraction | null,
): AllocationCategory | undefined {
  return categories(extraction).find(
    (category) => String(category.pool_key || '') === String(issue.category_key || ''),
  );
}

function categoryName(
  issue: CCRPromotionIssue,
  extraction: Extraction | null,
): string {
  const match = categoryForIssue(issue, extraction);
  if (match?.pool_name) return String(match.pool_name);
  return 'This charge';
}

const ISSUE_COPY: Record<
  string,
  Pick<CCRIIssueCopy, 'whatHappened' | 'ownerImpact' | 'recommendation'>
> = {
  CCR_POOL_SOURCE_MISSING: {
    whatHappened: 'The supporting pages for this charge still need to be confirmed.',
    ownerImpact: 'Without that confirmation, we cannot verify that owner charges follow the document.',
    recommendation: 'Confirm the PDF pages that describe this charge.',
  },
  CCR_DECLARED_CATEGORY_MISSING: {
    whatHappened: 'The document describes a charge that is not yet included in the setup.',
    ownerImpact: 'Leaving it out could make owner charges incomplete.',
    recommendation: 'Create the recommended charge category.',
  },
  CCR_RESIDUAL_EXCLUSIONS_INCOMPLETE: {
    whatHappened: 'Some expenses could be counted in more than one charge.',
    ownerImpact: 'That could cause owners to be charged twice for the same expense.',
    recommendation: 'Use the recommended correction to keep each expense in one place.',
  },
  CCR_OWNERSHIP_PERCENT_AMBIGUOUS: {
    whatHappened: 'The ownership percentages can be read in two different formats.',
    ownerImpact: 'Choosing the wrong format would change each owner’s share.',
    recommendation: 'Choose the format printed in the PDF.',
  },
  CCR_UNIT_FACTORS_MISSING: {
    whatHappened: 'The per-home values needed to divide this charge are missing.',
    ownerImpact: 'Owner charges cannot be calculated until those values are entered.',
    recommendation: 'Enter the missing values for each home.',
  },
  CCR_SPECIFIED_VALUES_MISSING: {
    whatHappened: 'A fixed annual amount is missing for one or more participating homes.',
    ownerImpact: 'The charge cannot be approved until every participating home has its documented amount.',
    recommendation: 'Enter a positive fixed annual amount for every participating home.',
  },
  CCR_SPECIFIED_VALUES_INVALID: {
    whatHappened: 'The per-home amounts do not add up to the documented category total.',
    ownerImpact: 'Approving these values would create an assessment schedule that does not reconcile.',
    recommendation: 'Correct the per-home amounts so they match the documented annual or monthly total.',
  },
  CCR_BILLING_COMBINATION_UNSUPPORTED: {
    whatHappened: 'The charge type and billing schedule contradict each other.',
    ownerImpact: 'That combination cannot produce a supported owner billing schedule.',
    recommendation: 'Use recurring billing for regular charges or one-time billing for a separate special assessment.',
  },
  CCR_SETUP_TYPE_INCOMPATIBLE: {
    whatHappened: 'This charge assigns values to individual or selected homes.',
    ownerImpact: 'A whole-property or grouped setup cannot preserve those home-level instructions.',
    recommendation: 'Use the per-home setup choice.',
  },
  CCR_OPERATOR_ROSTER_INCOMPLETE: {
    whatHappened: 'The saved home-value list is incomplete, so the existing values were kept unchanged.',
    ownerImpact: 'Approval is paused to avoid dropping homes or applying values to the wrong owners.',
    recommendation: 'Replace the complete home list, including every home, before approval.',
  },
  CCR_EDITED_ENTITY_UNPROMOTABLE: {
    whatHappened: 'A saved correction is still incomplete.',
    ownerImpact: 'The incomplete correction prevents reliable owner charges.',
    recommendation: 'Review the suggested values and save the correction again.',
  },
};

interface CCRIIssueCopy {
  whatHappened: string;
  ownerImpact: string;
  recommendation: string;
}

const FALLBACK_ISSUE_COPY: CCRIIssueCopy = {
  whatHappened: 'One part of the charging instructions still needs a decision.',
  ownerImpact: 'Approval is paused so owner charges are not created from an uncertain rule.',
  recommendation: 'Review the PDF evidence and apply the recommended correction.',
};

export function buildIssueCard(
  issue: CCRPromotionIssue,
  extraction: Extraction | null,
): CCRIssueCardView {
  const copy = ISSUE_COPY[issue.code] || FALLBACK_ISSUE_COPY;
  return {
    heading: `${categoryName(issue, extraction)} needs your attention`,
    ...copy,
    evidence: issue.source_pages.map((page) => ({
      page,
      label: `View PDF page ${page}`,
    })),
  };
}

function changesOf(operation: CCRRecommendedOperation): Record<string, unknown> {
  const changes = operation.changes;
  return changes && typeof changes === 'object'
    ? (changes as Record<string, unknown>)
    : {};
}

function nonEmptyPages(value: unknown): value is number[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((page) => Number.isInteger(page) && Number(page) > 0)
  );
}

function sameValue(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function isUsableCCRRecommendation(
  recommendation: CCRRecommendedOperation,
  extraction: Extraction | null = null,
): boolean {
  if (recommendation.operation === 'set_ownership_percent_form') {
    return (
      Array.isArray(recommendation.allowed_values) &&
      recommendation.allowed_values.includes('fraction') &&
      recommendation.allowed_values.includes('points')
    );
  }
  if (
    recommendation.operation !== 'add' &&
    recommendation.operation !== 'update'
  ) {
    return false;
  }
  if (
    typeof recommendation.category_key !== 'string' ||
    !recommendation.category_key.trim()
  ) {
    return false;
  }
  if (recommendation.operation === 'add') {
    const pool = recommendation.pool;
    if (!pool || typeof pool !== 'object' || Array.isArray(pool)) return false;
    const payload = pool as Record<string, unknown>;
    return (
      payload.pool_key === recommendation.category_key &&
      typeof payload.pool_name === 'string' &&
      payload.pool_name.trim().length > 0 &&
      typeof payload.allocation_method === 'string' &&
      payload.allocation_method.trim().length > 0 &&
      typeof payload.recipient_scope === 'string' &&
      payload.recipient_scope.trim().length > 0 &&
      nonEmptyPages(payload.source_pages)
    );
  }

  const changes = changesOf(recommendation);
  const entries = Object.entries(changes);
  if (entries.length === 0) return false;
  if ('source_pages' in changes && !nonEmptyPages(changes.source_pages)) {
    return false;
  }
  if (
    entries.some(
      ([, value]) =>
        value == null ||
        (typeof value === 'string' && value.trim().length === 0) ||
        (Array.isArray(value) && value.length === 0),
    )
  ) {
    return false;
  }
  const current = categories(extraction).find(
    (category) => category.pool_key === recommendation.category_key,
  );
  return !current || entries.some(([key, value]) => !sameValue(current[key], value));
}

export function correctionActionLabel(
  operation: CCRRecommendedOperation,
): string {
  const kind = String(operation.operation || '');
  const changes = changesOf(operation);
  if (kind === 'add') return 'Create reserve category';
  if ('annual_amount' in changes) return 'Enter the missing amount';
  if ('recipient_scope' in changes || kind === 'set_ownership_percent_form') {
    return 'Choose who pays';
  }
  if (changes.pool_kind === 'separately_billed_special_assessment') {
    return 'Mark as separately billed';
  }
  if ('pool_kind' in changes) return 'Keep with regular expenses';
  if ('source_pages' in changes) return 'Confirm supporting pages';
  return 'Apply recommended correction';
}

export function buildCCRCorrectionAction(
  recommendation: CCRRecommendedOperation,
  reviewVersion: number,
  extraction: Extraction | null = null,
): CCRCorrectionAction | null {
  if (!isUsableCCRRecommendation(recommendation, extraction)) {
    return null;
  }
  if (
    recommendation.operation !== 'add' &&
    recommendation.operation !== 'update'
  ) {
    return null;
  }
  return {
    kind: 'operation',
    value: {
      ...recommendation,
      base_version: reviewVersion,
    } as CCRPoolCorrectionOperation,
  };
}

export async function executeCCRCorrection(
  action: CCRCorrectionAction,
  dependencies: CCRCorrectionDependencies,
): Promise<CCRCorrectionResult> {
  if (action.kind === 'operation') {
    await dependencies.saveOperation(action.value);
  } else if (action.kind === 'scalar') {
    await dependencies.saveScalar({
      field_path: action.fieldPath,
      old_value: action.oldValue,
      new_value: action.value,
    });
  } else {
    await dependencies.saveFactors(action.values);
  }
  try {
    return {
      status: 'refreshed',
      preview: await dependencies.refetchPreview(),
    };
  } catch (refreshError) {
    return { status: 'saved_refresh_failed', refreshError };
  }
}

function money(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? numeric.toLocaleString('en-US', {
        style: 'currency',
        currency: 'USD',
        maximumFractionDigits: 0,
      })
    : 'an amount confirmed during budgeting';
}

function joinNatural(items: string[]): string {
  if (items.length <= 1) return items[0] || 'Documented owner charges';
  return `${items.slice(0, -1).join(', ')} and ${items.at(-1)}`;
}

export function buildCCRReadySummary(extraction: Extraction | null) {
  const rows = categories(extraction);
  const charged = joinNatural(
    rows.map(
      (row) =>
        `${String(row.pool_name || 'Documented charge')} (${money(row.annual_amount)})`,
    ),
  );
  const scopes = new Set(rows.map((row) => String(row.recipient_scope || '')));
  const methods = new Set(rows.map((row) => String(row.allocation_method || '')));
  const separate = rows.some(
    (row) => row.pool_kind === 'separately_billed_special_assessment',
  );
  return {
    heading: 'Ready to approve',
    charged,
    whoPays:
      scopes.size === 1 && scopes.has('all_units')
        ? 'All owners pay these charges.'
        : 'Only the owners identified by the document pay each charge.',
    howDivided:
      methods.size === 1 && methods.has('equal')
        ? 'The charges are divided equally.'
        : 'Each charge is divided using the method stated in the document.',
    whenBilled: separate
      ? 'Regular expenses follow the normal billing schedule; separate charges are billed on their stated schedule.'
      : 'These are billed with regular expenses.',
  };
}

export function isCCRApprovalDisabled(
  preview: Pick<CCRPromotionPreview, 'approval_blocked' | 'issues'> | null,
  busy: boolean,
): boolean {
  return busy || !preview || preview.approval_blocked;
}

export function friendlyCCRError(
  error: unknown,
  fallback = 'We could not save that correction. Refresh the review and try again.',
): string {
  return parseFriendlyCCRApiError(error, fallback);
}
