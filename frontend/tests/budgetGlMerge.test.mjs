import assert from 'node:assert/strict';
import test from 'node:test';

import {
  budgetHistoryLineItemToEditorItem,
  commitBudgetGlMerge,
  fetchBudgetGlMergeSuggestions,
  mapEditorLineItemsToBudgetHistory,
  unmergeBudgetGlMergeApplication,
} from '../src/app/api/budgetHistory.ts';
import {
  buildBudgetGlIdentity,
  findMergeCandidates,
} from '../src/app/lib/glMerge.ts';
import {
  glMergeSuggestionStorageKey,
  readGlMergeSuggestionCache,
  writeGlMergeSuggestionCache,
} from '../src/app/lib/glMergeSuggestionCache.ts';

test('budget draft line-item mapping preserves merge metadata needed by GL merge UI', () => {
  const editorItem = budgetHistoryLineItemToEditorItem(
    {
      line_item_key: '5010',
      account_code: '5010',
      label: 'Elevators',
      normalized_label: 'elevators',
      section: 'Utilities',
      category: 'operating',
      fund_type: 'operating',
      annual_budget: 2400,
      projection: 2500,
      ytd_actual: 200,
      merged_count: 1,
      merged_gls: [
        {
          account_code: '5015',
          label: 'Elevator Service',
          line_item_key: '5015',
          normalized_label: 'elevator service',
          contributions: {
            annual_budget: 1800,
          },
        },
      ],
    },
    0,
  );

  assert.equal(editorItem.lineItemKey, '5010');
  assert.equal(editorItem.normalizedLabel, 'elevators');
  assert.equal(editorItem.fundType, 'operating');
  assert.equal(editorItem.mergedCount, 1);
  assert.equal(editorItem.mergedGls?.[0]?.label, 'Elevator Service');

  const [roundTripped] = mapEditorLineItemsToBudgetHistory([editorItem]);
  assert.equal(roundTripped.line_item_key, '5010');
  assert.equal(roundTripped.normalized_label, 'elevators');
  assert.equal(roundTripped.fund_type, 'operating');
  assert.equal(roundTripped.merged_count, 1);
  assert.equal(roundTripped.merged_gls[0].label, 'Elevator Service');
});

test('GL merge helpers build identity and filter same-section draft candidates', () => {
  const lineItems = [
    {
      id: 'a',
      category: 'operating',
      name: 'Elevators',
      label: 'Elevators',
      accountCode: 5010,
      lineItemKey: '5010',
      normalizedLabel: 'elevators',
      rawSection: 'Utilities',
      fundType: 'operating',
      annualBudget: 2400,
      ytdActual: 200,
      percentChange: 0,
    },
    {
      id: 'b',
      category: 'operating',
      name: 'Elevator Service',
      label: 'Elevator Service',
      accountCode: 5015,
      lineItemKey: '5015',
      normalizedLabel: 'elevator service',
      rawSection: 'Utilities',
      fundType: 'operating',
      annualBudget: 1800,
      ytdActual: 75,
      percentChange: 0,
    },
    {
      id: 'c',
      category: 'operating',
      name: 'Landscaping',
      label: 'Landscaping',
      accountCode: 6010,
      lineItemKey: '6010',
      normalizedLabel: 'landscaping',
      rawSection: 'Grounds',
      fundType: 'operating',
      annualBudget: 5000,
      ytdActual: 400,
      percentChange: 0,
    },
    {
      id: 'd',
      category: 'reserve_expense',
      name: 'Roof',
      label: 'Roof',
      accountCode: 9010,
      lineItemKey: '9010',
      normalizedLabel: 'roof',
      rawSection: 'Reserve Expenses',
      fundType: 'reserve',
      annualBudget: 10000,
      ytdActual: 0,
      percentChange: 0,
      readOnly: true,
    },
  ];

  assert.deepEqual(
    buildBudgetGlIdentity(lineItems[0]),
    {
      account_code: '5010',
      label: 'Elevators',
      normalized_label: 'elevators',
      line_item_key: '5010',
      section: 'Utilities',
      category: 'operating',
      fund_type: 'operating',
    },
  );

  assert.deepEqual(
    findMergeCandidates(lineItems, 'a').map((item) => item.id),
    ['b'],
  );
});

test('GL merge API helpers send optimistic-lock headers and hit merge endpoints', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];

  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, init });
    const body =
      String(url).includes('/suggest')
        ? JSON.stringify([])
        : JSON.stringify({
            merge_id: 11,
            application: {
              id: 7,
              merge_id: 11,
              property_id: 5,
              budget_draft_id: 22,
              assessment_setup_id: 9,
              source: 'manual',
              status: 'applied',
              match_strategy: null,
            },
            draft_version: 3,
          });
    return new Response(body, {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  try {
    await fetchBudgetGlMergeSuggestions(5);
    await commitBudgetGlMerge(
      5,
      {
        primary: {
          account_code: '5010',
          label: 'Elevators',
          normalized_label: 'elevators',
          line_item_key: '5010',
          section: 'Utilities',
          category: 'operating',
          fund_type: 'operating',
        },
        secondary: {
          account_code: '5015',
          label: 'Elevator Service',
          normalized_label: 'elevator service',
          line_item_key: '5015',
          section: 'Utilities',
          category: 'operating',
          fund_type: 'operating',
        },
        source: 'manual',
      },
      2,
    );
    await unmergeBudgetGlMergeApplication(5, 7, 3);
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(calls.length, 3);
  assert.match(String(calls[0].url), /\/hoa\/5\/budget\/merges\/suggest$/);
  assert.match(String(calls[1].url), /\/hoa\/5\/budget\/merges$/);
  assert.match(String(calls[2].url), /\/hoa\/5\/budget\/merges\/applications\/7\/unmerge$/);
  assert.equal(calls[1].init.headers['If-Match'], '2');
  assert.equal(calls[2].init.headers['If-Match'], '3');
});

test('GL merge suggestion cache persists per HOA + draft and tolerates bad storage data', () => {
  const storage = new Map();
  const storageLike = {
    getItem(key) {
      return storage.has(key) ? storage.get(key) : null;
    },
    setItem(key, value) {
      storage.set(key, value);
    },
    removeItem(key) {
      storage.delete(key);
    },
  };

  const key = glMergeSuggestionStorageKey('5', 22);
  assert.equal(key, 'budget-gl-merge-suggestions:5:22');

  writeGlMergeSuggestionCache(storageLike, '5', 22, {
    suggestions: [
      {
        primary_account_code: '5010',
        secondary_account_code: '5015',
        primary_label: 'Elevators',
        secondary_label: 'Elevator Service',
        primary_normalized_label: 'elevators',
        secondary_normalized_label: 'elevator service',
        confidence: 0.92,
        reason: 'Same vendor/service family.',
        local_only: false,
        wire_schema_sha256: 'abc',
      },
    ],
    dismissedKeys: ['5010::5015'],
  });

  assert.deepEqual(readGlMergeSuggestionCache(storageLike, '5', 22), {
    suggestions: [
      {
        primary_account_code: '5010',
        secondary_account_code: '5015',
        primary_label: 'Elevators',
        secondary_label: 'Elevator Service',
        primary_normalized_label: 'elevators',
        secondary_normalized_label: 'elevator service',
        confidence: 0.92,
        reason: 'Same vendor/service family.',
        local_only: false,
        wire_schema_sha256: 'abc',
      },
    ],
    dismissedKeys: ['5010::5015'],
  });

  storage.set(key, '{bad json');
  assert.deepEqual(readGlMergeSuggestionCache(storageLike, '5', 22), {
    suggestions: [],
    dismissedKeys: [],
  });
});
