import assert from 'node:assert/strict';
import test from 'node:test';

import { getErrorMessage } from '../src/app/lib/errors.ts';

test('getErrorMessage falls back when the message is blank', () => {
  // Over HTTP/2 a 502/504 has no statusText, so the thrown message is ''.
  assert.equal(getErrorMessage({ status: 504, message: '' }, 'Upload failed.'), 'Upload failed.');
  assert.equal(getErrorMessage({ status: 502, message: '  ' }, 'Upload failed.'), 'Upload failed.');
  assert.equal(getErrorMessage({ status: 400, message: 'Bad file' }, 'Upload failed.'), 'Bad file');
  assert.equal(getErrorMessage(null, 'Upload failed.'), 'Upload failed.');
});
