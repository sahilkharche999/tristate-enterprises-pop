import assert from 'node:assert/strict';
import test from 'node:test';

import { earliestSourcePage } from '../src/app/lib/incomeStatementDerived.ts';

test('earliestSourcePage returns the lowest parseable page among rows', () => {
  const rows = [
    { sourcePageOrCell: 'page 5' },
    { sourcePageOrCell: 'page 3' },
    { sourcePageOrCell: 'page 8' },
  ];
  assert.equal(earliestSourcePage(rows), 3);
});

test('earliestSourcePage ignores rows with a non-page citation', () => {
  const rows = [
    { sourcePageOrCell: 'Sheet1!B12' },
    { sourcePageOrCell: 'page 3' },
    { sourcePageOrCell: null },
  ];
  assert.equal(earliestSourcePage(rows), 3);
});

test('earliestSourcePage returns undefined when no row has a parseable page', () => {
  assert.equal(earliestSourcePage([{ sourcePageOrCell: 'Sheet1!B12' }, { sourcePageOrCell: null }]), undefined);
});

test('earliestSourcePage returns undefined for an empty row list', () => {
  assert.equal(earliestSourcePage([]), undefined);
});
