import assert from 'node:assert/strict';
import test from 'node:test';

import {
  combinedLineHint,
  issueAnchor,
  slicesBalance,
} from '../src/app/lib/allocationResolution.ts';

test('balanced slices preserve the source total', () => {
  assert.equal(
    slicesBalance(1000, [
      { pool_key: 'exception', semantic_category: 'named-category', slice_annual_amount: '400' },
      { pool_key: 'residual', semantic_category: 'remainder', slice_annual_amount: '600' },
    ]),
    0,
  );
});

test('over-allocation and under-allocation fail the splitter', () => {
  assert.ok(slicesBalance(1000, [
    { pool_key: 'exception', semantic_category: 'named-category', slice_annual_amount: '400' },
  ]) > 0);
  assert.ok(slicesBalance(1000, [
    { pool_key: 'exception', semantic_category: 'named-category', slice_annual_amount: '700' },
    { pool_key: 'residual', semantic_category: 'remainder', slice_annual_amount: '600' },
  ]) < 0);
});

test('readiness issue anchors are deep-linkable', () => {
  assert.equal(issueAnchor('pool:exception_costs'), 'allocation-pool:exception_costs');
  assert.match(issueAnchor('line:utilities'), /allocation-line/);
});

test('combined-line hint reads readiness details and ignores unrelated issues', () => {
  assert.equal(combinedLineHint([]), null);
  assert.deepEqual(
    combinedLineHint([
      { code: 'allocation_resolution_required', details: {} },
      {
        code: 'combined_line_requires_split',
        details: { line_label: 'Water & Sewer', category: 'water', pool_key: 'exceptions' },
      },
    ]),
    { lineLabel: 'Water & Sewer', category: 'water', poolKey: 'exceptions' },
  );
});
