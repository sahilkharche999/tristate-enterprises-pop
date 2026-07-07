import assert from 'node:assert/strict';
import test from 'node:test';

import { clampSplitPercent } from '../src/app/components/resizableSplitPane.ts';

test('clampSplitPercent passes through values within range', () => {
  assert.equal(clampSplitPercent(50), 50);
  assert.equal(clampSplitPercent(35), 35);
});

test('clampSplitPercent clamps below the minimum', () => {
  assert.equal(clampSplitPercent(5), 20);
  assert.equal(clampSplitPercent(-100), 20);
});

test('clampSplitPercent clamps above the maximum', () => {
  assert.equal(clampSplitPercent(95), 80);
  assert.equal(clampSplitPercent(1000), 80);
});
