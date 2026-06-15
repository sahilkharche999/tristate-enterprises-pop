import assert from 'node:assert/strict';
import test from 'node:test';

import {
  fileDropzoneClassName,
  fileDropzoneHelperCopy,
} from '../src/app/components/fileDropzoneStyles.ts';

test('file dropzone idle state is clearly interactive', () => {
  const cls = fileDropzoneClassName({
    disabled: false,
    dragActive: false,
    fileName: null,
    status: 'idle',
  });

  assert.match(cls, /cursor-pointer/);
  assert.match(cls, /hover:/);
});

test('file dropzone selected state shows stronger emphasis', () => {
  const cls = fileDropzoneClassName({
    disabled: false,
    dragActive: false,
    fileName: 'budget.xlsx',
    status: 'selected',
  });

  assert.match(cls, /border-\[#111111\]/);
  assert.match(fileDropzoneHelperCopy({ dragActive: false, fileName: 'budget.xlsx' }), /Change file/i);
});

test('file dropzone drag state updates helper copy', () => {
  assert.match(fileDropzoneHelperCopy({ dragActive: true, fileName: null }), /Drop file here/i);
});
