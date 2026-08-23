import assert from 'node:assert/strict';
import test from 'node:test';

import {
  MISSOURI_CORRECTION,
  issueAnchor,
  slicesBalance,
} from '../src/app/lib/allocationResolution.ts';

test('Missouri electricity/gas split preserves the source total', () => {
  assert.equal(
    slicesBalance(MISSOURI_CORRECTION.sourceAnnual, MISSOURI_CORRECTION.slices),
    0,
  );
});

test('over-allocation and under-allocation fail the splitter', () => {
  assert.ok(slicesBalance(16800, [
    { pool_key: 'variable_dre_exceptions', semantic_category: 'gas', slice_annual_amount: '5600' },
  ]) > 0);
  assert.ok(slicesBalance(16800, [
    { pool_key: 'variable_dre_exceptions', semantic_category: 'gas', slice_annual_amount: '9000' },
    { pool_key: 'equal_base', semantic_category: 'electricity', slice_annual_amount: '11200' },
  ]) < 0);
});

test('readiness issue anchors are deep-linkable', () => {
  assert.equal(issueAnchor('pool:variable_dre_exceptions'), 'allocation-pool:variable_dre_exceptions');
  assert.match(issueAnchor('line:electricity gas'), /allocation-line/);
});

test('Missouri levy unit 201 stays the correction target', () => {
  assert.equal(MISSOURI_CORRECTION.levyUnit201, 1057.2);
});
