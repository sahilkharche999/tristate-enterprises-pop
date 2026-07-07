import assert from 'node:assert/strict';
import test from 'node:test';

import { comparePdfPaneMessage } from '../src/app/components/drePdfCompareViewStyles.ts';

test('comparePdfPaneMessage shows a loading message while fetching', () => {
  assert.match(comparePdfPaneMessage('loading') ?? '', /loading/i);
});

test('comparePdfPaneMessage shows an error message on fetch failure', () => {
  assert.match(comparePdfPaneMessage('error') ?? '', /could not load|failed/i);
});

test('comparePdfPaneMessage has no message once the PDF is ready to render', () => {
  assert.equal(comparePdfPaneMessage('ready'), null);
});
