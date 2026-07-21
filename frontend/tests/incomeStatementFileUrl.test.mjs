import assert from 'node:assert/strict';
import test from 'node:test';

import {
  incomeStatementFileUrl,
  incomeStatementHtmlFileUrl,
} from '../src/app/api/fileUrls.ts';

test('incomeStatementFileUrl builds the per-HOA per-upload income-statement path', () => {
  const url = incomeStatementFileUrl(7, 42);
  assert.match(url, /\/hoa\/7\/budget\/uploads\/42\/income-statement-file$/);
});

test('incomeStatementHtmlFileUrl builds the html file path', () => {
  const url = incomeStatementHtmlFileUrl(7, 42);
  assert.match(url, /\/hoa\/7\/budget\/uploads\/42\/income-statement-file-html$/);
});

test('income statement URL builders differ by suffix and ids', () => {
  assert.notEqual(incomeStatementFileUrl(1, 1), incomeStatementHtmlFileUrl(1, 1));
  assert.notEqual(incomeStatementFileUrl(1, 1), incomeStatementFileUrl(2, 1));
  assert.notEqual(incomeStatementFileUrl(1, 1), incomeStatementFileUrl(1, 2));
});
