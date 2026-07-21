import assert from 'node:assert/strict';
import test from 'node:test';

import { reserveStudyFileUrl } from '../src/app/api/fileUrls.ts';

test('reserveStudyFileUrl builds the per-HOA per-upload file path', () => {
  const url = reserveStudyFileUrl(7, 42);
  assert.match(url, /\/hoa\/7\/budget\/uploads\/42\/file$/);
});

test('reserveStudyFileUrl differs across HOAs and uploads', () => {
  assert.notEqual(reserveStudyFileUrl(1, 1), reserveStudyFileUrl(2, 1));
  assert.notEqual(reserveStudyFileUrl(1, 1), reserveStudyFileUrl(1, 2));
});
