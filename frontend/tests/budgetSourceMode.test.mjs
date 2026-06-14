import assert from 'node:assert/strict';
import test from 'node:test';

import {
  BUDGET_SOURCE_MODE_OPTIONS,
  budgetSourceModeCreateSuccess,
  budgetSourceModeHelperCopy,
  budgetSourceModeLabel,
  budgetSourceModeMissingUploadError,
  budgetSourceModeUploadPlaceholder,
  createBudgetBundleFormData,
  createBudgetSourceFormData,
} from '../src/app/lib/budgetSourceMode.ts';

test('budget source mode labels and copy switch by mode', () => {
  assert.deepEqual(
    BUDGET_SOURCE_MODE_OPTIONS.map((option) => option.value),
    ['income_statement', 'proforma_final_budget'],
  );

  assert.equal(budgetSourceModeLabel('income_statement'), 'Income Statement');
  assert.equal(budgetSourceModeLabel('proforma_final_budget'), 'Pro Forma / Final Budget');
  assert.match(budgetSourceModeHelperCopy('income_statement'), /income statement/i);
  assert.match(budgetSourceModeHelperCopy('proforma_final_budget'), /jan-dec/i);
  assert.match(budgetSourceModeUploadPlaceholder('proforma_final_budget'), /pro forma/i);
  assert.match(budgetSourceModeCreateSuccess('proforma_final_budget'), /pro forma/i);
  assert.match(budgetSourceModeMissingUploadError('proforma_final_budget'), /pro forma/i);
});

test('budget upload form-data builders include source_mode', () => {
  const budgetFile = new File(['budget'], 'budget.xlsx', {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
  const reserveFile = new File(['reserve'], 'reserve.pdf', { type: 'application/pdf' });

  const budgetOnlyFormData = createBudgetSourceFormData(budgetFile, 'proforma_final_budget');
  assert.equal(budgetOnlyFormData.get('source_mode'), 'proforma_final_budget');
  assert.equal(budgetOnlyFormData.get('file')?.name, 'budget.xlsx');

  const bundleFormData = createBudgetBundleFormData(
    budgetFile,
    reserveFile,
    'income_statement',
  );
  assert.equal(bundleFormData.get('source_mode'), 'income_statement');
  assert.equal(bundleFormData.get('budget_file')?.name, 'budget.xlsx');
  assert.equal(bundleFormData.get('reserve_study_file')?.name, 'reserve.pdf');
});
