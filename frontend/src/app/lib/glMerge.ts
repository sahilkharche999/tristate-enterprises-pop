import type {
  BudgetGlIdentityPayload,
  BudgetGlMergeSuggestionPayload,
} from '../api/budgetHistory.ts';
import type { LineItem } from '../data/mockData.ts';

function normalizeLabel(value: string | null | undefined): string {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ');
}

function normalizedAccountCode(item: LineItem): string | null {
  if (item.accountCode == null) {
    return null;
  }
  return String(item.accountCode);
}

function derivedFundType(item: LineItem): string {
  if (item.fundType?.trim()) {
    return item.fundType;
  }
  return item.category.startsWith('reserve') ? 'reserve' : 'operating';
}

function derivedSection(item: LineItem): string {
  if (item.rawSection?.trim()) {
    return item.rawSection;
  }
  return item.category === 'income' ? 'income' : 'expense';
}

export function buildBudgetGlIdentity(item: LineItem): BudgetGlIdentityPayload {
  return {
    account_code: normalizedAccountCode(item),
    label: item.label ?? item.name,
    normalized_label: item.normalizedLabel ?? normalizeLabel(item.label ?? item.name),
    line_item_key: item.lineItemKey ?? String(item.accountCode ?? item.label ?? item.name),
    section: derivedSection(item),
    category: item.category,
    fund_type: derivedFundType(item),
  };
}

export function findMergeCandidates(lineItems: LineItem[], primaryId: string): LineItem[] {
  const primary = lineItems.find((item) => item.id === primaryId);
  if (!primary) {
    return [];
  }

  const primarySection = normalizeLabel(derivedSection(primary));
  const primaryFundType = normalizeLabel(derivedFundType(primary));

  return lineItems.filter((candidate) => {
    if (candidate.id === primary.id || candidate.readOnly) {
      return false;
    }
    if (candidate.category !== primary.category) {
      return false;
    }
    if (normalizeLabel(derivedSection(candidate)) !== primarySection) {
      return false;
    }
    if (normalizeLabel(derivedFundType(candidate)) !== primaryFundType) {
      return false;
    }
    return true;
  });
}

export function mergeSuggestionKey(suggestion: BudgetGlMergeSuggestionPayload): string {
  return [
    suggestion.primary_account_code ?? suggestion.primary_normalized_label,
    suggestion.secondary_account_code ?? suggestion.secondary_normalized_label,
  ].join('::');
}

export function findLineItemByMergeIdentity(
  lineItems: LineItem[],
  identity: {
    accountCode?: string | null;
    normalizedLabel?: string | null;
    label?: string | null;
  },
): LineItem | null {
  const accountCode = identity.accountCode?.trim();
  if (accountCode) {
    const matches = lineItems.filter(
      (item) => !item.readOnly && String(item.accountCode ?? '') === accountCode,
    );
    if (matches.length === 1) {
      return matches[0];
    }
  }

  const normalizedLabel = normalizeLabel(identity.normalizedLabel ?? identity.label ?? null);
  if (!normalizedLabel) {
    return null;
  }

  const matches = lineItems.filter((item) => {
    if (item.readOnly) {
      return false;
    }
    return normalizeLabel(item.normalizedLabel ?? item.label ?? item.name) === normalizedLabel;
  });
  return matches.length === 1 ? matches[0] : null;
}

export function resolveMergeSuggestionItems(
  lineItems: LineItem[],
  suggestion: BudgetGlMergeSuggestionPayload,
): { primary: LineItem; secondary: LineItem } | null {
  const primary = findLineItemByMergeIdentity(lineItems, {
    accountCode: suggestion.primary_account_code,
    normalizedLabel: suggestion.primary_normalized_label,
    label: suggestion.primary_label,
  });
  const secondary = findLineItemByMergeIdentity(lineItems, {
    accountCode: suggestion.secondary_account_code,
    normalizedLabel: suggestion.secondary_normalized_label,
    label: suggestion.secondary_label,
  });
  if (!primary || !secondary || primary.id === secondary.id) {
    return null;
  }
  return { primary, secondary };
}

export function mergedBadgeLabel(item: LineItem): string | null {
  const mergedCount = item.mergedCount ?? item.mergedGls?.length ?? 0;
  if (mergedCount <= 0) {
    return null;
  }
  if (mergedCount === 1) {
    const label = item.mergedGls?.[0]?.label?.trim();
    return label ? `Merged with ${label}` : 'Merged with 1 row';
  }
  return `Merged with ${mergedCount} rows`;
}

export function mergedBadgeTooltip(item: LineItem): string | null {
  if (!item.mergedGls?.length) {
    return null;
  }
  return item.mergedGls
    .map((merged) => {
      const annualBudget = merged.contributions?.annual_budget;
      return annualBudget != null
        ? `${merged.label ?? 'Merged row'}: annual ${annualBudget}`
        : merged.label ?? 'Merged row';
    })
    .join('\n');
}
