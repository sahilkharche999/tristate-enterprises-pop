/**
 * Unit tests for budget entry CTA honesty (active draft vs post-generate).
 */
import assert from 'node:assert/strict';
import test from 'node:test';

const {
  resolveBudgetEntryCta,
  canShowOpenCurrentDraft,
} = await import('../src/app/lib/budgetEntryState.ts');

test('active draft → Open Current Draft', () => {
  const cta = resolveBudgetEntryCta({
    hoaId: 3,
    hasActiveDraft: true,
    latestVersionId: 99,
  });
  assert.equal(cta.mode, 'active_draft');
  assert.equal(cta.label, 'Open Current Draft');
  assert.equal(cta.href, '/hoa/3');
  assert.equal(canShowOpenCurrentDraft(true), true);
});

test('no draft + version → open latest generated, not Open Current Draft', () => {
  const cta = resolveBudgetEntryCta({
    hoaId: '3',
    hasActiveDraft: false,
    latestVersionId: 42,
    latestVersionCode: 'v3',
  });
  assert.equal(cta.mode, 'latest_generated');
  assert.match(cta.label, /Open latest generated/i);
  assert.ok(!cta.label.toLowerCase().includes('current draft'));
  assert.equal(cta.href, '/hoa/3?generated=true&versionId=42&readOnly=1');
  assert.equal(cta.secondaryLabel, 'Create new budget draft');
  assert.equal(cta.secondaryHref, '/hoa/3?create=1');
  assert.equal(canShowOpenCurrentDraft(false), false);
});

test('neither draft nor version → Create Budget Draft', () => {
  const cta = resolveBudgetEntryCta({
    hoaId: 7,
    hasActiveDraft: false,
    latestVersionId: null,
  });
  assert.equal(cta.mode, 'create_draft');
  assert.equal(cta.label, 'Create Budget Draft');
  assert.equal(cta.href, '/hoa/7');
});

test('draft only (no version) still opens current draft', () => {
  const cta = resolveBudgetEntryCta({
    hoaId: 1,
    hasActiveDraft: true,
    latestVersionId: null,
  });
  assert.equal(cta.mode, 'active_draft');
  assert.equal(cta.label, 'Open Current Draft');
});
