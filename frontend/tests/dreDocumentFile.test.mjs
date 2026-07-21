import assert from 'node:assert/strict';
import test from 'node:test';

import { dreDocumentFileUrl } from '../src/app/api/fileUrls.ts';

test('dreDocumentFileUrl builds the per-HOA per-document file path', () => {
  const url = dreDocumentFileUrl(7, 42);
  assert.match(url, /\/hoa\/7\/dre\/documents\/42\/file$/);
});

test('dreDocumentFileUrl differs across HOAs and documents', () => {
  assert.notEqual(dreDocumentFileUrl(1, 1), dreDocumentFileUrl(2, 1));
  assert.notEqual(dreDocumentFileUrl(1, 1), dreDocumentFileUrl(1, 2));
});
