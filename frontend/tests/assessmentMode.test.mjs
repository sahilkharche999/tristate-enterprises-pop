import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ASSESSMENT_MODE_OPTIONS,
  assessmentModeHelperCopy,
  assessmentModeLabel,
} from '../src/app/lib/assessmentMode.ts';
import {
  createBudgetBundleFormData,
  createBudgetSourceFormData,
} from '../src/app/lib/budgetSourceMode.ts';

test('assessment mode labels and helper copy explain fixed vs variable separately from source mode', () => {
  assert.deepEqual(
    ASSESSMENT_MODE_OPTIONS.map((option) => option.value),
    ['fixed', 'variable'],
  );

  assert.equal(assessmentModeLabel('fixed'), 'Fixed / Uniform Per Unit');
  assert.equal(assessmentModeLabel('variable'), 'Mixed / Variable');
  assert.match(assessmentModeHelperCopy('fixed'), /same dues/i);
  assert.match(assessmentModeHelperCopy('variable'), /dre/i);
});

test('budget upload form-data builders include assessment_mode', () => {
  const budgetFile = new File(['budget'], 'budget.xlsx', {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
  const reserveFile = new File(['reserve'], 'reserve.pdf', { type: 'application/pdf' });

  const budgetOnlyFormData = createBudgetSourceFormData(
    budgetFile,
    'proforma_final_budget',
    'fixed',
  );
  assert.equal(budgetOnlyFormData.get('assessment_mode'), 'fixed');

  const bundleFormData = createBudgetBundleFormData(
    budgetFile,
    reserveFile,
    'income_statement',
    'variable',
  );
  assert.equal(bundleFormData.get('assessment_mode'), 'variable');
});
