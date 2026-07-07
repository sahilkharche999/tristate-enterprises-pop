import assert from 'node:assert/strict';
import test from 'node:test';

import { clampTableZoomPercent, TABLE_ZOOM_STEP } from '../src/app/components/tableZoom.ts';

test('clampTableZoomPercent passes through values within range', () => {
  assert.equal(clampTableZoomPercent(80), 80);
  assert.equal(clampTableZoomPercent(100), 100);
});

test('clampTableZoomPercent clamps below the minimum', () => {
  assert.equal(clampTableZoomPercent(10), 50);
  assert.equal(clampTableZoomPercent(-40), 50);
});

test('clampTableZoomPercent clamps above the maximum', () => {
  assert.equal(clampTableZoomPercent(500), 150);
});

test('TABLE_ZOOM_STEP is a positive divisor of the zoom range', () => {
  assert.ok(TABLE_ZOOM_STEP > 0);
});
