/**
 * Tests for budget-draft-reserve-fidelity frontend changes.
 *
 * Covers:
 *   - calcTotalIncome / net surplus-deficit semantics (Issue 4)
 *   - read_only_override round-trip through budgetHistoryLineItemToEditorItem /
 *     mapEditorLineItemsToBudgetHistory (Issue 5)
 *   - liveTransferTotal computation (Issue 6 live-mirror)
 *   - totalAnnualBudget definition unchanged by new fields
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  budgetHistoryLineItemToEditorItem,
  mapEditorLineItemsToBudgetHistory,
  mapBudgetHistoryLineItems,
} from '../src/app/api/budgetHistory.ts';

import {
  calcTotalIncome,
  calcDisplayCategoryTotal,
  calcDisplayProposed,
} from '../src/app/lib/budget.ts';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeRecord(overrides = {}) {
  return {
    line_item_key: 'acct-test',
    account_code: '5010',
    label: 'Test Line',
    normalized_label: 'test line',
    category: 'operating',
    annual_budget: 10000,
    ytd_actual: 8000,
    projection: 10000,
    percent_change: 0,
    read_only: false,
    read_only_override: null,
    merged_count: 0,
    merged_gls: [],
    ...overrides,
  };
}

function makeItem(overrides = {}) {
  return budgetHistoryLineItemToEditorItem(makeRecord(overrides), 0);
}

function makeIncomeItem(annualBudget, percentChange = 0) {
  return makeItem({ category: 'income', annual_budget: annualBudget, percent_change: percentChange });
}

function makeExpenseItem(annualBudget, percentChange = 0) {
  return makeItem({ category: 'operating', annual_budget: annualBudget, percent_change: percentChange });
}

function makeReserveExpenseItem(annualBudget, reserveGroup = 'component') {
  return makeItem({
    category: 'reserve_expense',
    reserve_group: reserveGroup,
    annual_budget: annualBudget,
    read_only: true,
    read_only_override: null,
  });
}

function makeReserveIncomeItem(annualBudget, reserveGroup = null) {
  return makeItem({
    category: 'reserve_income',
    reserve_group: reserveGroup,
    annual_budget: annualBudget,
    read_only: true,
    read_only_override: null,
  });
}

// Mirror of liveTransferTotal computation in EnrichedView.tsx
function computeLiveTransferTotal(lineItems) {
  return lineItems.reduce((sum, item) => {
    if (item.category === 'reserve_expense' && item.reserveGroup === 'transfer') {
      return sum + (item.annualBudget || 0);
    }
    return sum;
  }, 0);
}

// Mirror of net surplus/deficit computation used in EnrichedView.tsx
function computeNet(lineItems, reserveInflationRate = null) {
  const incomeItems = lineItems.filter((it) => it.category === 'income');
  const expenseItems = lineItems.filter(
    (it) => it.category === 'operating' || it.category === 'reserve_expense',
  );
  const income = calcTotalIncome(incomeItems, reserveInflationRate);
  const expense = calcDisplayCategoryTotal(expenseItems, 'proposedChange', reserveInflationRate);
  return income - expense;
}

// ---------------------------------------------------------------------------
// Issue 4 — P&L summary
// ---------------------------------------------------------------------------

test('balanced budget produces net = 0', () => {
  const items = [makeIncomeItem(60000), makeExpenseItem(60000)];
  const net = computeNet(items);
  assert.equal(net, 0);
});

test('income > expense produces positive net (surplus)', () => {
  const items = [makeIncomeItem(80000), makeExpenseItem(60000)];
  const net = computeNet(items);
  assert.ok(net > 0, `expected surplus (net > 0), got ${net}`);
  assert.equal(net, 20000);
});

test('expense > income produces negative net (deficit)', () => {
  const items = [makeIncomeItem(50000), makeExpenseItem(60000)];
  const net = computeNet(items);
  assert.ok(net < 0, `expected deficit (net < 0), got ${net}`);
  assert.equal(net, -10000);
});

test('calcTotalIncome sums all income-category items using proposedChange', () => {
  const items = [
    makeIncomeItem(40000, 0),    // proposed = 40000
    makeIncomeItem(20000, 10),   // proposed = 22000
  ];
  const total = calcTotalIncome(items);
  assert.equal(total, 62000);
});

test('calcTotalIncome with percent change reflects proposed amounts', () => {
  const item = makeIncomeItem(100000, 5);  // proposed = 105000
  assert.equal(calcTotalIncome([item]), 105000);
});

test('read-only income items excluded from calcTotalIncome (not board-adjustable)', () => {
  const editableIncome = makeIncomeItem(60000);
  const readOnlyIncome = makeItem({ category: 'income', annual_budget: 20000, read_only: true });
  const total = calcTotalIncome([editableIncome, readOnlyIncome]);
  // readOnly item excluded — only editable income counts
  assert.equal(total, 60000);
});

test('totalAnnualBudget definition unchanged: does not include income category', () => {
  // totalAnnualBudget only sums operating/reserve expenses (not income)
  const items = [
    makeIncomeItem(60000),
    makeExpenseItem(40000),
    makeReserveExpenseItem(10000),
  ];
  // calcDisplayCategoryTotal with operating+reserve gives 40000 (reserve is read-only, excluded at default)
  const expItems = items.filter((it) => it.category === 'operating' || it.category === 'reserve_expense');
  const expTotal = calcDisplayCategoryTotal(expItems, 'proposedChange');
  assert.equal(expTotal, 40000, 'read-only reserve rows excluded from expense total at default');
});

test('net recomputes when an income line amount changes', () => {
  const incomeItem = makeIncomeItem(60000);
  const expenseItem = makeExpenseItem(60000);
  const initial = computeNet([incomeItem, expenseItem]);
  assert.equal(initial, 0);

  // Simulate editing the income item's annualBudget to 70000
  const updated = { ...incomeItem, annualBudget: 70000 };
  const after = computeNet([updated, expenseItem]);
  assert.equal(after, 10000, 'net should reflect increased income');
});

// ---------------------------------------------------------------------------
// Issue 5 — read_only_override round-trip
// ---------------------------------------------------------------------------

test('read_only_override=false deserializes correctly from backend record', () => {
  const record = makeRecord({
    category: 'reserve_income',
    read_only: true,
    read_only_override: false,
  });
  const item = budgetHistoryLineItemToEditorItem(record, 0);
  assert.equal(item.readOnlyOverride, false, 'readOnlyOverride should be false');
});

test('read_only_override=null deserializes as null (use category default)', () => {
  const record = makeRecord({ category: 'reserve_expense', read_only: true, read_only_override: null });
  const item = budgetHistoryLineItemToEditorItem(record, 0);
  assert.equal(item.readOnlyOverride, null);
});

test('read_only_override missing from record deserializes as null', () => {
  const { read_only_override: _, ...record } = makeRecord({ category: 'reserve_expense', read_only: true });
  const item = budgetHistoryLineItemToEditorItem(record, 0);
  assert.equal(item.readOnlyOverride, null);
});

test('read_only_override=false serializes to read_only_override=false in backend payload', () => {
  const item = makeItem({ category: 'reserve_income', read_only: true, read_only_override: false });
  // Override the readOnlyOverride field on the editor item
  const modified = { ...item, readOnlyOverride: false };
  const serialized = mapEditorLineItemsToBudgetHistory([modified]);
  assert.equal(serialized[0].read_only_override, false, 'override must be forwarded to API payload');
});

test('read_only_override=null serializes as null (re-lock signal)', () => {
  const item = { ...makeItem({ category: 'reserve_expense', read_only: true }), readOnlyOverride: null };
  const serialized = mapEditorLineItemsToBudgetHistory([item]);
  assert.equal(serialized[0].read_only_override, null);
});

test('read_only_override round-trip: false survives serialize → deserialize', () => {
  const original = makeItem({
    category: 'reserve_income',
    read_only: true,
    read_only_override: null,
  });
  // Simulate operator unlocking the line
  const unlocked = { ...original, readOnlyOverride: false };

  const serialized = mapEditorLineItemsToBudgetHistory([unlocked]);
  // Simulate backend echoing the same payload back as line_items
  const reloaded = mapBudgetHistoryLineItems(serialized);

  assert.equal(reloaded[0].readOnlyOverride, false, 'override=false must survive the round-trip');
});

test('read_only_override on operating line preserved through round-trip', () => {
  const item = makeItem({ category: 'operating', read_only: false });
  // Lock an operating line (unusual but supported)
  const locked = { ...item, readOnlyOverride: true };

  const serialized = mapEditorLineItemsToBudgetHistory([locked]);
  const reloaded = mapBudgetHistoryLineItems(serialized);

  assert.equal(reloaded[0].readOnlyOverride, true, 'true override on operating line preserved');
});

// ---------------------------------------------------------------------------
// Issue 6 — live transfer total (reserve-income mirror)
// ---------------------------------------------------------------------------

test('liveTransferTotal sums transfer reserve_expense lines only', () => {
  const items = [
    makeReserveExpenseItem(20000, 'transfer'),
    makeReserveExpenseItem(5000, 'component'),    // not a transfer — excluded
    makeReserveIncomeItem(3000),                  // income — excluded
  ];
  const total = computeLiveTransferTotal(items);
  assert.equal(total, 20000);
});

test('multiple transfer lines aggregate', () => {
  const items = [
    makeReserveExpenseItem(15000, 'transfer'),
    makeReserveExpenseItem(9000, 'transfer'),
  ];
  assert.equal(computeLiveTransferTotal(items), 24000);
});

test('no transfer lines → liveTransferTotal is 0', () => {
  const items = [makeReserveExpenseItem(10000, 'component')];
  assert.equal(computeLiveTransferTotal(items), 0);
});

test('unlocked reserve_income line included in income total when readOnly=false', () => {
  // When operator unlocks a reserve_income line (readOnly=false via override),
  // calcTotalIncome should now count it since readOnly is false.
  const unlockedIncome = {
    ...makeReserveIncomeItem(8000),
    readOnly: false,       // simulates the override having been applied
    readOnlyOverride: false,
  };
  const editableIncome = makeIncomeItem(40000);
  const total = calcTotalIncome([editableIncome, unlockedIncome]);
  // Both are non-readOnly — both should count
  assert.equal(total, 48000, 'unlocked reserve_income contributes to income total');
});
