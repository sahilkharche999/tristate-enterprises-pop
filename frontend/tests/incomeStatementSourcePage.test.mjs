import assert from 'node:assert/strict';
import test from 'node:test';

import { parseSourcePage } from '../src/app/lib/incomeStatementSourcePage.ts';

test('parseSourcePage parses "page N"', () => {
  assert.equal(parseSourcePage('page 4'), 4);
});

test('parseSourcePage is case-insensitive', () => {
  assert.equal(parseSourcePage('Page 12'), 12);
});

test('parseSourcePage returns null for an Excel cell reference', () => {
  assert.equal(parseSourcePage('Sheet1!B12'), null);
});

test('parseSourcePage returns null for null/undefined', () => {
  assert.equal(parseSourcePage(null), null);
  assert.equal(parseSourcePage(undefined), null);
});

test('parseSourcePage returns null when there is no number', () => {
  assert.equal(parseSourcePage('page'), null);
});

test('parseSourcePage returns null for trailing garbage after the number', () => {
  assert.equal(parseSourcePage('page 4x'), null);
});
