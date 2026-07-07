import assert from 'node:assert/strict';
import test from 'node:test';

import { earliestReserveStudyPage } from '../src/app/lib/reserveStudyDerived.ts';

test('earliestReserveStudyPage returns the lowest source_page among rows', () => {
  const rows = [
    { source_page: 5 },
    { source_page: 3 },
    { source_page: 8 },
  ];
  assert.equal(earliestReserveStudyPage(rows), 3);
});

test('earliestReserveStudyPage ignores rows with no source_page', () => {
  const rows = [
    { row_type: 'header', line_item: 'BUILDING SYSTEMS' },
    { source_page: 3 },
    {},
  ];
  assert.equal(earliestReserveStudyPage(rows), 3);
});

test('earliestReserveStudyPage returns undefined when no row has a source_page', () => {
  assert.equal(earliestReserveStudyPage([{ row_type: 'header' }, {}]), undefined);
});

test('earliestReserveStudyPage returns undefined for an empty row list', () => {
  assert.equal(earliestReserveStudyPage([]), undefined);
});
