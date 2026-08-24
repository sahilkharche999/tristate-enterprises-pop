import { useEffect, useMemo, useRef, useState } from 'react';

import type {
  CCRPoolCorrectionOperation,
  CCRUnitFactorEntry,
} from '../api/ccr';
import {
  buildAdvancedFactorPayload,
  buildAdvancedCategoryPool,
  displayCategoryName,
  generateCategoryKey,
  type CCRAdvancedCategoryDraft,
  type CCRFactorDraft,
} from '../lib/ccrReviewWorkflow';

type Props = {
  categories: Array<Record<string, unknown>>;
  unitStructure?: {
    unit_count?: unknown;
    units?: Array<Record<string, unknown>>;
  };
  reviewVersion: number;
  previewIdentity: string;
  previewRevision: number;
  disabled: boolean;
  jumpToPage: (page: number) => void;
  onSave: (
    operation: CCRPoolCorrectionOperation,
    reason: string,
    factors?: CCRUnitFactorEntry[],
  ) => Promise<boolean>;
};

const EMPTY_DRAFT: CCRAdvancedCategoryDraft = {
  name: '',
  includedExpenses: '',
  billing: 'regular',
  cadence: 'recurring',
  amountAvailability: 'known',
  amount: '',
  recipientScope: 'all_units',
  participantUnitNumbers: [],
  allocation: 'equal',
  sourcePages: '',
};

function factorDrafts(
  units: Array<Record<string, unknown>>,
): CCRFactorDraft[] {
  return units.map((unit) => ({
    unit_number: String(unit.unit_number || ''),
    square_feet: unit.square_feet == null ? '' : String(unit.square_feet),
    ownership_percent:
      unit.ownership_percent == null ? '' : String(unit.ownership_percent),
    fixed_amounts: Object.fromEntries(
      (Array.isArray(unit.pool_factors) ? unit.pool_factors : [])
        .filter(
          (factor) =>
            typeof factor === 'object' &&
            factor != null &&
            String((factor as Record<string, unknown>).factor_type) ===
              'dollar_amount',
        )
        .map((factor) => {
          const value = factor as Record<string, unknown>;
          return [
            String(value.pool_key || ''),
            value.factor_value == null ? '' : String(value.factor_value),
          ];
        }),
    ),
    custom_factors: Object.fromEntries(
      (Array.isArray(unit.pool_factors) ? unit.pool_factors : [])
        .filter(
          (factor) =>
            typeof factor === 'object' &&
            factor != null &&
            String((factor as Record<string, unknown>).factor_type) !==
              'dollar_amount',
        )
        .map((factor) => {
          const value = factor as Record<string, unknown>;
          return [
            String(value.pool_key || ''),
            value.factor_value == null ? '' : String(value.factor_value),
          ];
        }),
    ),
  }));
}

function evidencedUnitNumbers(
  scope: CCRAdvancedCategoryDraft['recipientScope'],
  units: Array<Record<string, unknown>>,
): string[] {
  if (scope === 'all_units' || scope === 'custom_unit_list') return [];
  return units
    .filter((unit) => {
      const category = String(
        unit.category || unit.residential_commercial_flag || '',
      ).toLowerCase();
      const parking = String(
        unit.parking_flag || unit.parking_spaces || '',
      ).toLowerCase();
      if (scope === 'residential_only') return category.startsWith('res');
      if (scope === 'commercial_only') return category.startsWith('com');
      return !['', '0', 'false', 'no', 'none'].includes(parking);
    })
    .map((unit) => String(unit.unit_number || '').trim())
    .filter(Boolean);
}

function categoryDraft(
  category?: Record<string, unknown>,
  units: Array<Record<string, unknown>> = [],
): CCRAdvancedCategoryDraft {
  if (!category) return { ...EMPTY_DRAFT };
  const allocation = String(category.allocation_method || 'equal');
  const recipientScope = [
    'all_units',
    'residential_only',
    'commercial_only',
    'parking_users',
    'custom_unit_list',
  ].includes(String(category.recipient_scope || ''))
    ? (category.recipient_scope as CCRAdvancedCategoryDraft['recipientScope'])
    : 'custom_unit_list';
  const savedParticipants = Array.isArray(category.selected_unit_numbers)
    ? category.selected_unit_numbers.map(String)
    : Array.isArray(category.participant_unit_numbers)
      ? category.participant_unit_numbers.map(String)
      : [];
  const billing =
    category.pool_kind === 'separately_billed_special_assessment'
      ? 'separate'
      : 'regular';
  return {
    name: displayCategoryName(category.pool_name),
    includedExpenses: Array.isArray(category.included_budget_lines)
      ? category.included_budget_lines.join(', ')
      : '',
    billing,
    cadence: billing === 'separate' ? 'one_time' : 'recurring',
    amountAvailability:
      category.amount_availability === 'external_schedule' ||
      category.amount_availability === 'operator_pending'
        ? category.amount_availability
        : 'known',
    amount: category.annual_amount == null ? '' : String(category.annual_amount),
    recipientScope,
    participantUnitNumbers:
      recipientScope === 'all_units'
        ? []
        : savedParticipants.length > 0
          ? savedParticipants
          : evidencedUnitNumbers(recipientScope, units),
    allocation:
      allocation === 'specified_value'
        ? 'fixed_amount'
        : allocation === 'custom_factor'
          ? 'external_schedule'
          : (allocation as CCRAdvancedCategoryDraft['allocation']),
    sourcePages: Array.isArray(category.source_pages)
      ? category.source_pages.join(', ')
      : '',
  };
}

function CategoryFields({
  draft,
  onChange,
  unitNumbers,
  evidenceUnits,
}: {
  draft: CCRAdvancedCategoryDraft;
  onChange: (draft: CCRAdvancedCategoryDraft) => void;
  unitNumbers: string[];
  evidenceUnits: Array<Record<string, unknown>>;
}) {
  const update = <K extends keyof CCRAdvancedCategoryDraft>(
    field: K,
    value: CCRAdvancedCategoryDraft[K],
  ) => onChange({ ...draft, [field]: value });
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <label className="text-sm font-medium text-slate-800">
        Category name
        <input
          aria-label="Category name"
          value={draft.name}
          onChange={(event) => update('name', event.target.value)}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
        />
      </label>
      <label className="text-sm font-medium text-slate-800">
        Who pays
        <select
          aria-label="Who pays"
          value={draft.recipientScope}
          onChange={(event) => {
            const recipientScope = event.target
              .value as CCRAdvancedCategoryDraft['recipientScope'];
            onChange({
              ...draft,
              recipientScope,
              participantUnitNumbers: evidencedUnitNumbers(
                recipientScope,
                evidenceUnits,
              ),
            });
          }}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
        >
          <option value="all_units">All homes</option>
          <option value="residential_only">Residential homes only</option>
          <option value="commercial_only">Commercial homes only</option>
          <option value="parking_users">Homes with parking</option>
          <option value="custom_unit_list">Selected homes</option>
        </select>
      </label>
      {draft.recipientScope !== 'all_units' && (
        <fieldset className="rounded-md border border-slate-200 p-3 md:col-span-2">
          <legend className="px-1 text-sm font-medium text-slate-800">
            Choose every home that pays
          </legend>
          <div className="mt-2 flex flex-wrap gap-3">
            {unitNumbers.map((unitNumber) => (
              <label key={unitNumber} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  aria-label={`Include home ${unitNumber}`}
                  checked={draft.participantUnitNumbers.includes(unitNumber)}
                  onChange={(event) =>
                    update(
                      'participantUnitNumbers',
                      event.target.checked
                        ? [...draft.participantUnitNumbers, unitNumber]
                        : draft.participantUnitNumbers.filter(
                            (value) => value !== unitNumber,
                          ),
                    )
                  }
                />
                Home {unitNumber}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      <label className="text-sm font-medium text-slate-800 md:col-span-2">
        Included expenses
        <textarea
          aria-label="Included expenses"
          value={draft.includedExpenses}
          onChange={(event) => update('includedExpenses', event.target.value)}
          placeholder="Separate expenses with commas or new lines"
          className="mt-1 min-h-20 w-full rounded-md border border-slate-300 px-3 py-2"
        />
      </label>
      <label className="text-sm font-medium text-slate-800">
        How it is billed
        <select
          aria-label="How it is billed"
          value={draft.billing}
          onChange={(event) => {
            const billing = event.target
              .value as CCRAdvancedCategoryDraft['billing'];
            onChange({
              ...draft,
              billing,
              cadence: billing === 'separate' ? 'one_time' : 'recurring',
            });
          }}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
        >
          <option value="regular">With regular dues</option>
          <option value="separate">Billed separately</option>
        </select>
      </label>
      <label className="text-sm font-medium text-slate-800">
        Billing schedule
        <select
          aria-label="Billing schedule"
          value={draft.cadence}
          disabled
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
        >
          {draft.billing === 'separate' ? (
            <option value="one_time">One time</option>
          ) : (
            <option value="recurring">Recurring</option>
          )}
        </select>
      </label>
      <label className="text-sm font-medium text-slate-800">
        Amount source
        <select
          aria-label="Amount source"
          value={draft.amountAvailability}
          onChange={(event) =>
            update(
              'amountAvailability',
              event.target.value as CCRAdvancedCategoryDraft['amountAvailability'],
            )
          }
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
        >
          <option value="known">Amount is known</option>
          <option value="external_schedule">Amount comes from another schedule</option>
          <option value="operator_pending">Amount is still pending</option>
        </select>
      </label>
      {draft.amountAvailability === 'known' && (
        <label className="text-sm font-medium text-slate-800">
          Annual amount
          <input
            aria-label="Annual amount"
            type="number"
            min="0"
            value={draft.amount}
            onChange={(event) => update('amount', event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
          />
        </label>
      )}
      <label className="text-sm font-medium text-slate-800">
        How the charge is divided
        <select
          aria-label="How the charge is divided"
          value={draft.allocation}
          onChange={(event) =>
            update(
              'allocation',
              event.target.value as CCRAdvancedCategoryDraft['allocation'],
            )
          }
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
        >
          <option value="equal">Equally</option>
          <option value="square_footage">By square footage</option>
          <option value="ownership_percentage">By ownership percentage</option>
          <option value="fixed_amount">A fixed amount for each home</option>
          <option value="external_schedule">From another schedule</option>
        </select>
      </label>
      <label className="text-sm font-medium text-slate-800">
        Supporting PDF pages
        <input
          aria-label="Supporting PDF pages"
          value={draft.sourcePages}
          onChange={(event) => update('sourcePages', event.target.value)}
          placeholder="For example: 8, 9"
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2"
        />
      </label>
    </div>
  );
}

export function CCRAdvancedCorrections({
  categories,
  unitStructure,
  previewIdentity,
  previewRevision,
  reviewVersion,
  disabled,
  jumpToPage,
  onSave,
}: Props) {
  const sourceUnits = Array.isArray(unitStructure?.units)
    ? unitStructure.units
    : [];
  const [operation, setOperation] = useState('update');
  const [draft, setDraft] = useState<CCRAdvancedCategoryDraft>(
    categoryDraft(categories[0], sourceUnits),
  );
  const [selectedKey, setSelectedKey] = useState(
    String(categories[0]?.pool_key || ''),
  );
  const [splitDrafts, setSplitDrafts] = useState<
    [CCRAdvancedCategoryDraft, CCRAdvancedCategoryDraft]
  >([
    categoryDraft(categories[0], sourceUnits),
    { ...categoryDraft(categories[0], sourceUnits), name: '' },
  ]);
  const [mergeKeys, setMergeKeys] = useState<string[]>([]);
  const [factorRows, setFactorRows] = useState<CCRFactorDraft[]>(
    factorDrafts(sourceUnits),
  );
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const previewKey = `${previewIdentity}:${reviewVersion}:${previewRevision}`;
  const previousPreviewKey = useRef(previewKey);
  useEffect(() => {
    if (previousPreviewKey.current === previewKey) return;
    previousPreviewKey.current = previewKey;
    const first = categories[0];
    const firstDraft = categoryDraft(first, sourceUnits);
    setOperation('update');
    setSelectedKey(String(first?.pool_key || ''));
    setDraft(firstDraft);
    setSplitDrafts([firstDraft, { ...firstDraft, name: '' }]);
    setMergeKeys([]);
    setFactorRows(factorDrafts(sourceUnits));
    setReason('');
    setError(null);
  }, [previewKey]);
  const existingKeys = useMemo(
    () => new Set(categories.map((category) => String(category.pool_key || ''))),
    [categories],
  );
  const selectedCategory = categories.find(
    (category) => String(category.pool_key || '') === selectedKey,
  );
  const activeDrafts =
    operation === 'split'
      ? splitDrafts
      : operation === 'remove'
        ? []
        : [draft];
  const unitNumbers = factorRows
    .map((unit) => unit.unit_number.trim())
    .filter(Boolean);
  const replacementEntries = (() => {
    if (operation === 'add') {
      return [
        {
          key: generateCategoryKey(draft.name, existingKeys),
          draft,
        },
      ];
    }
    if (operation === 'update') return [{ key: selectedKey, draft }];
    if (operation === 'split') {
      const firstKey = generateCategoryKey(splitDrafts[0].name, existingKeys);
      return [
        { key: firstKey, draft: splitDrafts[0] },
        {
          key: generateCategoryKey(
            splitDrafts[1].name,
            new Set([...existingKeys, firstKey]),
          ),
          draft: splitDrafts[1],
        },
      ];
    }
    if (operation === 'merge') {
      return [
        {
          key: generateCategoryKey(
            draft.name,
            new Set([...existingKeys].filter((key) => !mergeKeys.includes(key))),
          ),
          draft,
        },
      ];
    }
    return [];
  })();
  const replacedKeys = new Set(
    operation === 'merge'
      ? mergeKeys
      : operation === 'add'
        ? []
        : [selectedKey],
  );
  const remainingCategories = categories.filter(
    (category) => !replacedKeys.has(String(category.pool_key || '')),
  );
  const remainingEntries = remainingCategories.map((category) => ({
    key: String(category.pool_key || ''),
    draft: categoryDraft(category, sourceUnits),
  }));
  const resultingEntries = [...remainingEntries, ...replacementEntries];
  const squareFeetCategories = resultingEntries.filter(
    (entry) => entry.draft.allocation === 'square_footage',
  );
  const ownershipPercentCategories = resultingEntries.filter(
    (entry) => entry.draft.allocation === 'ownership_percentage',
  );
  const squareFeetRequired = squareFeetCategories.length > 0;
  const ownershipPercentRequired = ownershipPercentCategories.length > 0;
  const participantUnion = (
    entries: typeof resultingEntries,
  ): string[] | undefined => {
    if (entries.some((entry) => entry.draft.recipientScope === 'all_units')) {
      return undefined;
    }
    return [
      ...new Set(
        entries.flatMap((entry) => entry.draft.participantUnitNumbers),
      ),
    ];
  };
  const squareFeetUnitNumbers = participantUnion(squareFeetCategories);
  const ownershipPercentUnitNumbers = participantUnion(
    ownershipPercentCategories,
  );
  const fixedCategories = resultingEntries.filter(
      (entry) => entry.draft.allocation === 'fixed_amount',
  );
  const customCategories = resultingEntries.filter(
    (entry) => entry.draft.allocation === 'external_schedule',
  );
  const needsFactors =
    squareFeetRequired ||
    ownershipPercentRequired ||
    fixedCategories.length > 0 ||
    customCategories.length > 0;
  const needsSubsetRoster = activeDrafts.some(
    (item) => item.recipientScope !== 'all_units',
  );

  function factorsForCurrentDraft(): CCRUnitFactorEntry[] | undefined | null {
    if (!needsFactors && !needsSubsetRoster) return undefined;
    if (!needsFactors) {
      const parsedCount = Number(unitStructure?.unit_count);
      const expectedCount =
        Number.isInteger(parsedCount) && parsedCount > 0 ? parsedCount : null;
      const roster = buildAdvancedFactorPayload(factorRows, expectedCount, {
        squareFeet: false,
        ownershipPercent: false,
        fixedCategoryKeys: [],
      });
      if (roster.error) {
        setError(roster.error);
        return null;
      }
      return roster.values.map((entry) => ({
        unit_number: entry.unit_number,
        square_feet: null,
        ownership_percent: null,
      }));
    }
    const parsedCount = Number(unitStructure?.unit_count);
    const expectedCount =
      Number.isInteger(parsedCount) && parsedCount > 0
        ? parsedCount
        : null;
    const payload = buildAdvancedFactorPayload(
      factorRows,
      expectedCount,
      {
        squareFeet: squareFeetRequired,
        ownershipPercent: ownershipPercentRequired,
        squareFeetUnitNumbers,
        ownershipPercentUnitNumbers,
        fixedCategoryKeys: fixedCategories.map((category) => category.key),
        fixedRecipientUnitNumbers: Object.fromEntries(
          fixedCategories
            .filter(
              (category) =>
                category.draft.recipientScope !== 'all_units',
            )
            .map((category) => [
              category.key,
              category.draft.participantUnitNumbers,
            ]),
        ),
        customCategoryKeys: customCategories.map((category) => category.key),
        customRecipientUnitNumbers: Object.fromEntries(
          customCategories
            .filter(
              (category) =>
                category.draft.recipientScope !== 'all_units',
            )
            .map((category) => [
              category.key,
              category.draft.participantUnitNumbers,
            ]),
        ),
      },
    );
    if (payload.error) {
      setError(payload.error);
      return null;
    }
    for (const category of fixedCategories) {
      const expected = Number(category.draft.amount);
      const total = payload.values.reduce(
        (sum, row) => sum + Number(row.fixed_amounts?.[category.key] || 0),
        0,
      );
      if (!Number.isFinite(expected) || Math.abs(total - expected) > 0.005) {
        setError(
          `The per-home fixed annual amounts for ${category.draft.name || 'this category'} must add up to its annual amount.`,
        );
        return null;
      }
    }
    return payload.values;
  }

  function requireReason(): boolean {
    if (reason.trim()) return true;
    setError('Tell us why you are making this correction.');
    return false;
  }

  function validateDraft(item: CCRAdvancedCategoryDraft): boolean {
    if (!item.name.trim()) {
      setError('Enter a category name before saving.');
      return false;
    }
    if (item.amountAvailability === 'known') {
      const amount = Number(item.amount);
      if (!item.amount.trim() || !Number.isFinite(amount) || amount <= 0) {
        setError('Enter a positive annual amount before saving.');
        return false;
      }
    }
    if (
      item.recipientScope !== 'all_units' &&
      item.participantUnitNumbers.length === 0
    ) {
      setError('Choose at least one home that pays this charge.');
      return false;
    }
    if (item.recipientScope !== 'all_units') {
      const entered = factorRows
        .map((row) => row.unit_number.trim())
        .filter(Boolean);
      if (new Set(entered.map((value) => value.toLowerCase())).size !== entered.length) {
        setError('Use a different home identifier on every row.');
        return false;
      }
      if (
        item.participantUnitNumbers.some(
          (unitNumber) => !entered.includes(unitNumber),
        )
      ) {
        setError('Choose participating homes from the current home list.');
        return false;
      }
    }
    return true;
  }

  function selectCategory(key: string) {
    setSelectedKey(key);
    const next = categories.find(
      (category) => String(category.pool_key || '') === key,
    );
    setDraft(categoryDraft(next, sourceUnits));
    setSplitDrafts([
      categoryDraft(next, sourceUnits),
      { ...categoryDraft(next, sourceUnits), name: '' },
    ]);
  }

  function changeOperation(next: string) {
    setOperation(next);
    setError(null);
    if (next === 'add' || next === 'merge') setDraft({ ...EMPTY_DRAFT });
    if (next === 'update') {
      setDraft(categoryDraft(selectedCategory, sourceUnits));
    }
    if (next === 'split') {
      setSplitDrafts([
        categoryDraft(selectedCategory, sourceUnits),
        { ...categoryDraft(selectedCategory, sourceUnits), name: '' },
      ]);
    }
  }

  async function saveAdd() {
    if (!requireReason()) return;
    if (!validateDraft(draft)) return;
    const key = generateCategoryKey(draft.name, existingKeys);
    const factors = factorsForCurrentDraft();
    if (factors === null) return;
    setError(null);
    const saved = await onSave(
      {
        operation: 'add',
        base_version: reviewVersion,
        category_key: key,
        pool: buildAdvancedCategoryPool(draft, key),
      },
      reason.trim(),
      factors,
    );
    if (saved) {
      setDraft(EMPTY_DRAFT);
      setReason('');
    }
  }

  async function saveUpdate() {
    if (!requireReason()) return;
    if (!selectedCategory) {
      setError('Choose a category and enter its name before saving.');
      return;
    }
    if (!validateDraft(draft)) return;
    const pool = buildAdvancedCategoryPool(draft, selectedKey);
    const factors = factorsForCurrentDraft();
    if (factors === null) return;
    const { pool_key: _stableKey, ...changes } = pool;
    setError(null);
    await onSave(
      {
        operation: 'update',
        base_version: reviewVersion,
        category_key: selectedKey,
        changes,
      },
      reason.trim(),
      factors,
    );
  }

  async function saveSplit() {
    if (!requireReason()) return;
    if (!selectedCategory || splitDrafts.some((item) => !item.name.trim())) {
      setError('Name both new categories before saving.');
      return;
    }
    if (splitDrafts.some((item) => !validateDraft(item))) return;
    const firstKey = generateCategoryKey(splitDrafts[0].name, existingKeys);
    const secondKey = generateCategoryKey(
      splitDrafts[1].name,
      new Set([...existingKeys, firstKey]),
    );
    const factors = factorsForCurrentDraft();
    if (factors === null) return;
    setError(null);
    await onSave(
      {
        operation: 'split',
        base_version: reviewVersion,
        category_key: selectedKey,
        pools: [
          buildAdvancedCategoryPool(splitDrafts[0], firstKey),
          buildAdvancedCategoryPool(splitDrafts[1], secondKey),
        ],
      },
      reason.trim(),
      factors,
    );
  }

  async function saveMerge() {
    if (!requireReason()) return;
    if (mergeKeys.length < 2) {
      setError('Choose at least two categories to combine.');
      return;
    }
    if (!validateDraft(draft)) return;
    const replacementKey = generateCategoryKey(
      draft.name,
      new Set([...existingKeys].filter((key) => !mergeKeys.includes(key))),
    );
    const factors = factorsForCurrentDraft();
    if (factors === null) return;
    setError(null);
    await onSave(
      {
        operation: 'merge',
        base_version: reviewVersion,
        category_keys: mergeKeys,
        pool: buildAdvancedCategoryPool(draft, replacementKey),
      },
      reason.trim(),
      factors,
    );
  }

  async function saveRemove() {
    if (!requireReason()) return;
    if (!selectedCategory) {
      setError('Choose a category to remove.');
      return;
    }
    setError(null);
    await onSave(
      {
        operation: 'remove',
        base_version: reviewVersion,
        category_key: selectedKey,
      },
      reason.trim(),
    );
  }
  const saveLabel = {
    add: 'Save new category',
    update: 'Save category changes',
    split: 'Save split',
    merge: 'Save combined category',
    remove: 'Remove category',
  }[operation];
  const saveCurrent = {
    add: saveAdd,
    update: saveUpdate,
    split: saveSplit,
    merge: saveMerge,
    remove: saveRemove,
  }[operation];

  return (
    <details className="rounded-xl border border-slate-200 bg-white">
      <summary className="cursor-pointer px-4 py-3 font-semibold text-slate-900">
        Advanced corrections
      </summary>
      <div className="space-y-5 border-t border-slate-200 p-4">
        <p className="text-sm text-slate-600">
          Add or reorganize charge categories when the guided choices above do not
          match the document.
        </p>
        {categories.some((category) => Array.isArray(category.source_pages)) && (
          <div className="space-y-2" aria-label="Category source evidence">
            {categories.map((category) => (
              <div key={String(category.pool_key)} className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-slate-800">
                  {displayCategoryName(category.pool_name)}
                </span>
                {(category.source_pages as number[] | undefined)?.map((page) => (
                  <button
                    key={page}
                    type="button"
                    onClick={() => jumpToPage(page)}
                    className="rounded-md border border-slate-300 px-2 py-1 text-xs"
                  >
                    View PDF page {page}
                  </button>
                ))}
              </div>
            ))}
          </div>
        )}
        <label className="block text-sm font-medium text-slate-800">
          What would you like to do?
          <select
            aria-label="Correction type"
            value={operation}
            disabled={disabled}
            onChange={(event) => changeOperation(event.target.value)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2"
          >
            <option value="update">Update a category</option>
            <option value="add">Add a category</option>
            <option value="split">Split a category</option>
            <option value="merge">Combine categories</option>
            <option value="remove">Remove a category</option>
          </select>
        </label>
        {(operation === 'update' ||
          operation === 'split' ||
          operation === 'remove') && (
          <label className="block text-sm font-medium text-slate-800">
            Category
            <select
              aria-label="Category to correct"
              value={selectedKey}
              disabled={disabled}
              onChange={(event) => selectCategory(event.target.value)}
              className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2"
            >
              {categories.map((category) => (
                <option
                  key={String(category.pool_key)}
                  value={String(category.pool_key)}
                >
                  {displayCategoryName(category.pool_name)}
                </option>
              ))}
            </select>
          </label>
        )}
        {operation === 'add' && (
          <div className="space-y-4">
            <CategoryFields draft={draft} onChange={setDraft} unitNumbers={unitNumbers} evidenceUnits={sourceUnits} />
          </div>
        )}
        {operation === 'update' && (
          <div className="space-y-4">
            <CategoryFields draft={draft} onChange={setDraft} unitNumbers={unitNumbers} evidenceUnits={sourceUnits} />
          </div>
        )}
        {operation === 'split' && (
          <div className="grid gap-4 lg:grid-cols-2">
            <fieldset className="space-y-3 rounded-lg border border-slate-200 p-3">
              <legend className="px-1 text-sm font-semibold text-slate-800">
                First category
              </legend>
              <CategoryFields
                draft={splitDrafts[0]}
                unitNumbers={unitNumbers}
                evidenceUnits={sourceUnits}
                onChange={(value) =>
                  setSplitDrafts([value, splitDrafts[1]])
                }
              />
            </fieldset>
            <fieldset className="space-y-3 rounded-lg border border-slate-200 p-3">
              <legend className="px-1 text-sm font-semibold text-slate-800">
                Second category
              </legend>
              <CategoryFields
                draft={splitDrafts[1]}
                unitNumbers={unitNumbers}
                evidenceUnits={sourceUnits}
                onChange={(value) =>
                  setSplitDrafts([splitDrafts[0], value])
                }
              />
            </fieldset>
          </div>
        )}
        {operation === 'merge' && (
          <div className="space-y-4">
            <fieldset>
              <legend className="text-sm font-medium text-slate-800">
                Categories to combine
              </legend>
              <div className="mt-2 flex flex-wrap gap-3">
                {categories.map((category) => {
                  const key = String(category.pool_key || '');
                  return (
                    <label key={key} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={mergeKeys.includes(key)}
                        onChange={(event) =>
                          setMergeKeys((current) =>
                            event.target.checked
                              ? [...current, key]
                              : current.filter((value) => value !== key),
                          )
                        }
                      />
                      {displayCategoryName(category.pool_name)}
                    </label>
                  );
                })}
              </div>
            </fieldset>
            <CategoryFields draft={draft} onChange={setDraft} unitNumbers={unitNumbers} evidenceUnits={sourceUnits} />
          </div>
        )}
        {operation === 'remove' && (
          <p className="text-sm text-slate-600">
            The selected category will be removed from the reviewed charges.
          </p>
        )}
        {(needsFactors || needsSubsetRoster) && operation !== 'remove' && (
          <section className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/40 p-3">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">
                {needsFactors
                  ? 'Enter the values for every home'
                  : 'Add homes for this payer group'}
              </h3>
              <p className="mt-0.5 text-xs text-slate-600">
                These values stay with this correction, so there is no separate
                setup screen.
              </p>
            </div>
            {factorRows.map((row, index) => (
              <fieldset
                key={index}
                className="grid gap-2 rounded-md border border-slate-200 bg-white p-3 sm:grid-cols-2"
              >
                <legend className="px-1 text-xs font-medium text-slate-600">
                  Home {index + 1}
                </legend>
                <label className="text-xs font-medium text-slate-700">
                  Home identifier
                  <input
                    aria-label={`Advanced home identifier ${index + 1}`}
                    value={row.unit_number}
                    onChange={(event) =>
                      setFactorRows((current) =>
                        current.map((item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, unit_number: event.target.value }
                            : item,
                        ),
                      )
                    }
                    className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                  />
                </label>
                {squareFeetRequired &&
                  (!squareFeetUnitNumbers ||
                    squareFeetUnitNumbers.includes(row.unit_number.trim())) && (
                  <label className="text-xs font-medium text-slate-700">
                    Square feet
                    <input
                      type="number"
                      min="0"
                      aria-label={`Square feet for advanced home ${index + 1}`}
                      value={row.square_feet}
                      onChange={(event) =>
                        setFactorRows((current) =>
                          current.map((item, itemIndex) =>
                            itemIndex === index
                              ? { ...item, square_feet: event.target.value }
                              : item,
                          ),
                        )
                      }
                      className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                    />
                  </label>
                  )}
                {ownershipPercentRequired &&
                  (!ownershipPercentUnitNumbers ||
                    ownershipPercentUnitNumbers.includes(
                      row.unit_number.trim(),
                    )) && (
                  <label className="text-xs font-medium text-slate-700">
                    Ownership percentage
                    <input
                      type="number"
                      min="0"
                      aria-label={`Ownership percentage for advanced home ${index + 1}`}
                      value={row.ownership_percent}
                      onChange={(event) =>
                        setFactorRows((current) =>
                          current.map((item, itemIndex) =>
                            itemIndex === index
                              ? {
                                  ...item,
                                  ownership_percent: event.target.value,
                                }
                              : item,
                          ),
                        )
                      }
                      className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                    />
                  </label>
                  )}
                {fixedCategories
                  .filter(
                    (category) =>
                      category.draft.recipientScope === 'all_units' ||
                      category.draft.participantUnitNumbers.includes(
                        row.unit_number.trim(),
                      ),
                  )
                  .map((category) => (
                  <label
                    key={category.key}
                    className="text-xs font-medium text-slate-700"
                  >
                    Fixed annual amount — {category.draft.name || 'category'}
                    <input
                      type="number"
                      min="0"
                      aria-label={`Fixed annual amount for ${category.draft.name || 'category'}, home ${index + 1}`}
                      value={row.fixed_amounts?.[category.key] ?? ''}
                      onChange={(event) =>
                        setFactorRows((current) =>
                          current.map((item, itemIndex) =>
                            itemIndex === index
                              ? {
                                  ...item,
                                  fixed_amounts: {
                                    ...item.fixed_amounts,
                                    [category.key]: event.target.value,
                                  },
                                }
                              : item,
                          ),
                        )
                      }
                      className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                    />
                  </label>
                  ))}
                {customCategories
                  .filter(
                    (category) =>
                      category.draft.recipientScope === 'all_units' ||
                      category.draft.participantUnitNumbers.includes(
                        row.unit_number.trim(),
                      ),
                  )
                  .map((category) => (
                    <label
                      key={category.key}
                      className="text-xs font-medium text-slate-700"
                    >
                      Custom factor — {category.draft.name || 'category'}
                      <input
                        type="number"
                        min="0"
                        aria-label={`Custom factor for ${category.draft.name || 'category'}, home ${index + 1}`}
                        value={row.custom_factors?.[category.key] || ''}
                        onChange={(event) =>
                          setFactorRows((current) =>
                            current.map((item, itemIndex) =>
                              itemIndex === index
                                ? {
                                    ...item,
                                    custom_factors: {
                                      ...item.custom_factors,
                                      [category.key]: event.target.value,
                                    },
                                  }
                                : item,
                            ),
                          )
                        }
                        className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                      />
                    </label>
                  ))}
              </fieldset>
            ))}
            <button
              type="button"
              disabled={disabled}
              onClick={() =>
                setFactorRows((current) => [
                  ...current,
                  {
                    unit_number: '',
                    square_feet: '',
                    ownership_percent: '',
                    fixed_amounts: {},
                    custom_factors: {},
                  },
                ])
              }
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium"
            >
              Add another home
            </button>
          </section>
        )}
        <label className="block text-sm font-medium text-slate-800">
          Reason for this correction
          <textarea
            aria-label="Reason for this correction"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            className="mt-1 min-h-20 w-full rounded-md border border-slate-300 px-3 py-2"
          />
        </label>
        {error && <p role="alert" className="text-sm text-rose-700">{error}</p>}
        {saveLabel && saveCurrent && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => void saveCurrent()}
            className={`rounded-md px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 ${
              operation === 'remove' ? 'bg-rose-700' : 'bg-slate-900'
            }`}
          >
            {saveLabel}
          </button>
        )}
      </div>
    </details>
  );
}
