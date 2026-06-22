import assert from 'node:assert/strict';
import test from 'node:test';

import {
  budgetHistoryLineItemToEditorItem,
  mapEditorLineItemsToBudgetHistory,
  mapBudgetHistoryLineItems,
} from '../src/app/api/budgetHistory.ts';

// Mirror of handleLineItemFieldChange in BudgetScreen.tsx
function applyFieldChange(lineItems, itemId, field, value) {
  return lineItems.map((item) => {
    if (item.id !== itemId) return item;
    if (field === 'name') {
      return { ...item, name: value, label: value };
    }
    const numValue = parseFloat(value) || 0;
    return { ...item, [field]: numValue };
  });
}

function makeItem(overrides = {}) {
  return budgetHistoryLineItemToEditorItem(
    {
      line_item_key: 'acct-5010',
      account_code: '5010',
      label: 'Utilities',
      normalized_label: 'utilities',
      category: 'operating',
      annual_budget: 43200,
      ytd_actual: 36000,
      projection: 43000,
      percent_change: 5,
      read_only: false,
      merged_count: 0,
      merged_gls: [],
      ...overrides,
    },
    0,
  );
}

function makeMergedItem() {
  return budgetHistoryLineItemToEditorItem(
    {
      line_item_key: 'acct-5010',
      account_code: '5010',
      label: 'Utilities',
      normalized_label: 'utilities',
      category: 'operating',
      annual_budget: 43200,
      ytd_actual: 36000,
      projection: 43000,
      percent_change: 5,
      read_only: false,
      merged_count: 1,
      merged_gls: [
        {
          account_code: '5015',
          label: 'Water',
          line_item_key: 'acct-5015',
          normalized_label: 'water',
          contributions: { annual_budget: 1200, ytd: 0, current_period: 0, projection: 0, variance: 0 },
        },
      ],
    },
    0,
  );
}

// 5.1 — editing each source field updates only that field
test('editing annualBudget updates only annualBudget on the target item', () => {
  const item0 = budgetHistoryLineItemToEditorItem(
    { line_item_key: 'acct-5010', account_code: '5010', label: 'Utilities', category: 'operating',
      annual_budget: 43200, ytd_actual: 36000, projection: 43000, percent_change: 5, read_only: false,
      merged_count: 0, merged_gls: [] }, 0);
  const item1 = budgetHistoryLineItemToEditorItem(
    { line_item_key: 'acct-6000', account_code: '6000', label: 'Other', category: 'operating',
      annual_budget: 43200, ytd_actual: 36000, projection: 43000, percent_change: 5, read_only: false,
      merged_count: 0, merged_gls: [] }, 1);
  const items = [item0, item1];
  const targetId = item0.id;
  const result = applyFieldChange(items, targetId, 'annualBudget', '45000');
  assert.equal(result[0].annualBudget, 45000);
  assert.equal(result[0].ytdActual, items[0].ytdActual, 'ytdActual unchanged');
  assert.equal(result[0].projection, items[0].projection, 'projection unchanged');
  assert.equal(result[0].percentChange, items[0].percentChange, 'percentChange unchanged');
  assert.deepEqual(result[1], items[1], 'second item unchanged');
});

test('editing ytdActual updates only ytdActual on the target item', () => {
  const items = [makeItem()];
  const result = applyFieldChange(items, items[0].id, 'ytdActual', '12000');
  assert.equal(result[0].ytdActual, 12000);
  assert.equal(result[0].annualBudget, items[0].annualBudget);
});

test('editing projection updates only projection on the target item', () => {
  const items = [makeItem()];
  const result = applyFieldChange(items, items[0].id, 'projection', '44000');
  assert.equal(result[0].projection, 44000);
  assert.equal(result[0].annualBudget, items[0].annualBudget);
});

// 5.3 — empty numeric input resolves to 0
test('empty string for a numeric field resolves to 0', () => {
  const items = [makeItem()];
  const result = applyFieldChange(items, items[0].id, 'ytdActual', '');
  assert.equal(result[0].ytdActual, 0);
});

test('non-numeric string for a numeric field resolves to 0', () => {
  const items = [makeItem()];
  const result = applyFieldChange(items, items[0].id, 'annualBudget', 'abc');
  assert.equal(result[0].annualBudget, 0);
});

// 5.4 — name edit preserves identity fields
test('editing name preserves lineItemKey, normalizedLabel, and accountCode', () => {
  const items = [makeItem()];
  const before = items[0];
  const result = applyFieldChange(items, before.id, 'name', 'Electric & Water');
  const after = result[0];
  assert.equal(after.name, 'Electric & Water');
  assert.equal(after.label, 'Electric & Water', 'label also updated');
  assert.equal(after.lineItemKey, before.lineItemKey, 'lineItemKey unchanged');
  assert.equal(after.normalizedLabel, before.normalizedLabel, 'normalizedLabel unchanged');
  assert.equal(after.accountCode, before.accountCode, 'accountCode unchanged');
});

// 5.6 — editing a merged-primary line leaves merge state intact
test('editing a merged primary line leaves mergedGls and mergedCount intact', () => {
  const items = [makeMergedItem()];
  const before = items[0];
  assert.equal(before.mergedCount, 1);
  assert.equal(before.mergedGls.length, 1);

  const afterName = applyFieldChange(items, before.id, 'name', 'Renamed')[0];
  assert.equal(afterName.mergedCount, 1, 'mergedCount preserved after name edit');
  assert.equal(afterName.mergedGls.length, 1, 'mergedGls preserved after name edit');

  const afterBudget = applyFieldChange(items, before.id, 'annualBudget', '50000')[0];
  assert.equal(afterBudget.mergedCount, 1, 'mergedCount preserved after budget edit');
  assert.equal(afterBudget.mergedGls.length, 1, 'mergedGls preserved after budget edit');
  assert.equal(afterBudget.annualBudget, 50000);
});

// 5.7 — round-trip: edited fields persist through mapEditorLineItemsToBudgetHistory
test('edited source fields round-trip through mapEditorLineItemsToBudgetHistory and mapBudgetHistoryLineItems', () => {
  const original = makeItem();
  let items = [original];

  items = applyFieldChange(items, original.id, 'name', 'Fixed Label');
  items = applyFieldChange(items, original.id, 'annualBudget', '45000');
  items = applyFieldChange(items, original.id, 'ytdActual', '38000');
  items = applyFieldChange(items, original.id, 'projection', '46000');

  const serialized = mapEditorLineItemsToBudgetHistory(items);
  const reloaded = mapBudgetHistoryLineItems(serialized);

  assert.equal(reloaded[0].name, 'Fixed Label');
  assert.equal(reloaded[0].annualBudget, 45000);
  assert.equal(reloaded[0].ytdActual, 38000);
  assert.equal(reloaded[0].projection, 46000);
  assert.equal(reloaded[0].lineItemKey, original.lineItemKey, 'lineItemKey preserved through round-trip');
});

// 5.5 — read-only items: confirmed by mapEditorLineItemsToBudgetHistory round-trip preserving readOnly flag
test('read-only flag survives the mapping round-trip (readOnly items remain flagged)', () => {
  const readOnlyRecord = {
    line_item_key: 'rsv-001',
    account_code: '90001',
    label: 'Reserve Component',
    normalized_label: 'reserve component',
    category: 'reserve_expense',
    annual_budget: 12000,
    ytd_actual: 0,
    projection: 12000,
    percent_change: 0,
    read_only: true,
    merged_count: 0,
    merged_gls: [],
  };
  const item = budgetHistoryLineItemToEditorItem(readOnlyRecord, 0);
  assert.equal(item.readOnly, true, 'readOnly flag deserialized correctly');
  const serialized = mapEditorLineItemsToBudgetHistory([item]);
  assert.equal(serialized[0].readOnly, true, 'readOnly preserved on serialization');
  assert.equal(serialized[0].read_only, true, 'read_only snake_case preserved');
});
