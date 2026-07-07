import assert from 'node:assert/strict';
import test from 'node:test';

import { pdfPageAnchorUrl } from '../src/app/components/pdfPageAnchor.ts';

test('pdfPageAnchorUrl appends a #page=N fragment', () => {
  assert.equal(pdfPageAnchorUrl('blob:abc123', 4), 'blob:abc123#page=4');
});

test('pdfPageAnchorUrl returns the url unchanged when page is undefined', () => {
  assert.equal(pdfPageAnchorUrl('blob:abc123', undefined), 'blob:abc123');
});

test('pdfPageAnchorUrl returns the url unchanged for an empty base url', () => {
  assert.equal(pdfPageAnchorUrl('', 4), '');
});
