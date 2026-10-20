import assert from 'node:assert/strict';
import test from 'node:test';

import {
  BUDGET_SOURCE_MODE_OPTIONS,
  budgetSourceModeCreateSuccess,
  budgetSourceModeLabel,
  createBudgetBundleFormData,
  createBudgetSourceFormData,
} from '../src/app/lib/budgetSourceMode.ts';

test('budget source mode labels and copy name the detected kind', () => {
  assert.deepEqual(
    BUDGET_SOURCE_MODE_OPTIONS.map((option) => option.value),
    ['income_statement', 'proforma_final_budget'],
  );

  assert.equal(budgetSourceModeLabel('income_statement'), 'Income Statement');
  assert.equal(budgetSourceModeLabel('proforma_final_budget'), 'Pro Forma / Final Budget');
  assert.equal(budgetSourceModeCreateSuccess('proforma_final_budget'), 'Draft created from a final budget.');
  assert.equal(budgetSourceModeCreateSuccess('income_statement'), 'Draft created from an income statement.');
});

test('budget upload form-data builders send source_mode only as an override', () => {
  const budgetFile = new File(['budget'], 'budget.xlsx', {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
  const reserveFile = new File(['reserve'], 'reserve.pdf', { type: 'application/pdf' });

  const detected = createBudgetSourceFormData(budgetFile, null);
  assert.equal(detected.has('source_mode'), false);
  assert.equal(detected.get('file')?.name, 'budget.xlsx');
  assert.equal(
    createBudgetSourceFormData(budgetFile, 'proforma_final_budget').get('source_mode'),
    'proforma_final_budget',
  );

  assert.equal(createBudgetBundleFormData(budgetFile, reserveFile, null).has('source_mode'), false);
  const bundleFormData = createBudgetBundleFormData(
    budgetFile,
    reserveFile,
    'income_statement',
  );
  assert.equal(bundleFormData.get('source_mode'), 'income_statement');
  assert.equal(bundleFormData.get('budget_file')?.name, 'budget.xlsx');
  assert.equal(bundleFormData.get('reserve_study_file')?.name, 'reserve.pdf');
});
