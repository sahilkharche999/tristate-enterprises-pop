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

export function displayCategoryName(name: unknown): string {
  const stripped = String(name || '')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\s+pools?\s*$/i, '')
    .trim();
  return stripped || 'Documented charge';
}

function categoryName(
  issue: CCRPromotionIssue,
  extraction: Extraction | null,
): string {
  const match = categoryForIssue(issue, extraction);
  if (match?.pool_name) return displayCategoryName(match.pool_name);
  return 'This charge';
}

export function friendlyAllocationMethod(method: unknown): string {
  switch (String(method || '')) {
    case 'equal':
      return 'Divided equally';
    case 'square_footage':
      return 'Divided by square footage';
    case 'ownership_percentage':
      return 'Divided by ownership percentage';
    case 'specified_value':
      return 'A fixed amount for each home';
    case 'custom_factor':
      return 'Divided using an external schedule';
    default:
      return 'Division method still needs review';
  }
}

export function friendlyWhoPays(scope: unknown, selected?: unknown): string {
  const value = String(scope || 'all_units');
  if (value === 'all_units') return 'All homes';
  if (value === 'residential_only') return 'Residential homes only';
  if (value === 'commercial_only') return 'Commercial homes only';
  if (value === 'parking_users' || /parking/i.test(value)) {
    return 'Homes with parking';
  }
  if (value === 'custom_unit_list') {
    const homes = Array.isArray(selected)
      ? selected.map((item) => String(item || '').trim()).filter(Boolean)
      : [];
    return homes.length > 0 ? `Selected homes: ${homes.join(', ')}` : 'Selected homes';
  }
  return 'The homes named in the document';
}

export function friendlyBillingTreatment(
  category: AllocationCategory,
): string {
  const separate =
    category.pool_kind === 'separately_billed_special_assessment' ||
    category.allocation_context === 'special_assessment' ||
    category.billing_treatment === 'separate_one_time' ||
    category.billing_cadence === 'one_time';
  return separate ? 'Billed separately' : 'With regular dues';
}

export function friendlyCadence(category: AllocationCategory): string {
  return category.billing_cadence === 'one_time' ||
    category.allocation_context === 'special_assessment'
    ? 'One-time'
    : 'Recurring';
}

export function friendlyAmountSource(availability: unknown): string {
  switch (String(availability || '')) {
    case 'known':
      return 'Amount is known';
    case 'external_schedule':
      return 'Uses the DRE / budget schedule';
    case 'operator_pending':
      return 'Amount still needs to be entered';
    default:
      return 'Amount is not in this document';
  }
}

export function friendlyAmountDisplay(
  annualAmount: unknown,
  availability: unknown,
): string {
  if (annualAmount != null && String(annualAmount).trim() !== '') {
    const numeric = Number(annualAmount);
    if (Number.isFinite(numeric) && numeric > 0) {
      return `${numeric.toLocaleString('en-US', {
        style: 'currency',
        currency: 'USD',
        maximumFractionDigits: 0,
      })} per year`;
    }
  }
  if (String(availability || '') === 'external_schedule') {
    return 'Uses the DRE / budget schedule';
  }
  if (String(availability || '') === 'operator_pending') {
    return 'Amount still needs to be entered';
  }
  return 'Amount is not in this document';
}

export function friendlyPageType(pageType: unknown): string {
  switch (String(pageType || '')) {
    case 'assessment/allocation provisions':
      return 'Assessment and allocation rules';
    case 'special assessment provisions':
      return 'Special assessment rules';
    case 'exhibit/percentage-interest table':
      return 'Home share table';
    case 'definitions':
      return 'Definitions';
    case 'use restrictions':
      return 'Use restrictions';
    case 'maintenance responsibilities':
      return 'Maintenance responsibilities';
    case 'condominium plan/floor plan':
      return 'Floor plan';
    case 'governance/voting':
      return 'Governance';
    case 'insurance provisions':
      return 'Insurance';
    case 'enforcement/dispute resolution':
      return 'Enforcement';
    case 'signature/notary':
      return 'Signature page';
    case 'table of contents/index':
      return 'Table of contents';
    case 'recitals/preamble':
      return 'Introduction';
    case 'blank/irrelevant':
      return 'Not used for charges';
    default: {
      const raw = String(pageType || '').trim();
      return raw
        ? raw.replace(/[_/]+/g, ' ').replace(/\s+/g, ' ')
        : 'Document page';
    }
  }
}

function hasList(value: unknown): boolean {
  return Array.isArray(value) && value.length > 0;
}

function numberPages(value: unknown): number[] {
  return Array.isArray(value)
    ? value
        .map((page) => Number(page))
        .filter((page) => Number.isInteger(page) && page > 0)
    : [];
}

function coversText(row: AllocationCategory): string {
  const lines = row.included_budget_lines || row.expense_categories;
  if (Array.isArray(lines) && lines.length > 0) {
    return lines.map(String).map((line) => line.trim()).filter(Boolean).join(', ');
  }
  return 'Expenses not listed separately in another charge';
}

export function mergeExtractionForDetail(
  resolved: Record<string, unknown> | null | undefined,
  parsed: Record<string, unknown> | null | undefined,
): Record<string, unknown> | null {
  if (!resolved && !parsed) return null;
  const merged = { ...(parsed || {}), ...(resolved || {}) };
  if (resolved && parsed) {
    if (!hasList(resolved.page_inventory) && hasList(parsed.page_inventory)) {
      merged.page_inventory = parsed.page_inventory;
    }
    if (!resolved.document_metadata && parsed.document_metadata) {
      merged.document_metadata = parsed.document_metadata;
    }
    if (
      !hasList(resolved.human_review_questions) &&
      hasList(parsed.human_review_questions)
    ) {
      merged.human_review_questions = parsed.human_review_questions;
    }
  }
  return merged;
}

export type CCRExtractedDetailView = {
  hoa: {
    associationName: string;
    documentTitle: string;
    documentDate: string;
    unitCount: string;
    sourcePages: number[];
    purpose: string;
  };
  division: {
    summary: string;
    needsExternalBudget: boolean;
    purpose: string;
  };
  categories: Array<{
    name: string;
    covers: string;
    whoPays: string;
    howDivided: string;
    billedWith: string;
    cadence: string;
    amount: string;
    amountSource: string;
    sourcePages: number[];
  }>;
  homes: Array<{
    unitNumber: string;
    squareFeet: string;
    ownershipPercent: string;
  }>;
  pages: Array<{ pageNumber: number; pageType: string; notes: string }>;
  questions: Array<{
    question: string;
    reason: string;
    sourcePages: number[];
  }>;
};

export function buildCCRExtractedDetail(
  extraction: Record<string, unknown> | null,
): CCRExtractedDetailView | null {
  if (!extraction) return null;
  const meta = (extraction.document_metadata || {}) as Record<string, unknown>;
  const setup = (extraction.assessment_setup || {}) as Record<string, unknown>;
  const unitStructure = (extraction.unit_structure || {}) as Record<
    string,
    unknown
  >;
  const units = Array.isArray(unitStructure.units) ? unitStructure.units : [];
  const pages = Array.isArray(extraction.page_inventory)
    ? extraction.page_inventory
    : [];
  const questions = Array.isArray(extraction.human_review_questions)
    ? extraction.human_review_questions
    : [];

  return {
    hoa: {
      associationName: String(meta.association_name || '').trim(),
      documentTitle: String(meta.document_title || '').trim(),
      documentDate: String(meta.document_date || '').trim(),
      unitCount: String(
        meta.total_units ?? unitStructure.unit_count ?? (units.length || ''),
      ),
      sourcePages: numberPages(meta.source_pages ?? setup.source_pages),
      purpose: 'Confirms we opened the right governing document.',
    },
    division: {
      summary: String(setup.summary || '').trim(),
      needsExternalBudget: Boolean(setup.requires_dre_for_future_years),
      purpose: 'Explains how the document divides owner charges.',
    },
    categories: categories(extraction).map((row) => ({
      name: displayCategoryName(row.pool_name),
      covers: coversText(row),
      whoPays: friendlyWhoPays(row.recipient_scope, row.selected_unit_numbers),
      howDivided: friendlyAllocationMethod(
        row.allocation_method || row.allocation_basis,
      ),
      billedWith: friendlyBillingTreatment(row),
      cadence: friendlyCadence(row),
      amount: friendlyAmountDisplay(row.annual_amount, row.amount_availability),
      amountSource: friendlyAmountSource(row.amount_availability),
      sourcePages: numberPages(row.source_pages),
    })),
    homes: units.map((unit) => {
      const row = unit as Record<string, unknown>;
      return {
        unitNumber: String(row.unit_number || '').trim(),
        squareFeet: row.square_feet == null ? '' : String(row.square_feet),
        ownershipPercent:
          row.ownership_percent == null ? '' : String(row.ownership_percent),
      };
    }),
    pages: pages
      .map((entry) => {
        const row = entry as Record<string, unknown>;
        return {
          pageNumber: Number(row.page_number) || 0,
          pageType: friendlyPageType(row.page_type),
          notes: String(row.notes || '').trim(),
        };
      })
      .filter((row) => row.pageNumber > 0),
    questions: questions
      .map((entry) => {
        const row = entry as Record<string, unknown>;
        return {
          question: String(row.question || '').trim(),
          reason: String(row.reason || '').trim(),
          sourcePages: numberPages(row.source_pages),
        };
      })
      .filter((row) => row.question),
  };
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
  if (value == null || String(value).trim() === '') {
    return 'an amount confirmed during budgeting';
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0
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
        `${displayCategoryName(row.pool_name)} (${money(row.annual_amount)})`,
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
