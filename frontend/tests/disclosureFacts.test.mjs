import assert from 'node:assert/strict';
import test from 'node:test';

import { disclosureFactsSummary, sameFactValue } from '../src/app/lib/disclosureFacts.ts';

test('disclosure facts toast counts filled settings and suggestions, not matches', () => {
  assert.equal(
    disclosureFactsSummary([
      { status: 'applied' },
      { status: 'applied' },
      { status: 'suggested' },
      { status: 'matches' },
    ]),
    'Filled 2 HOA settings from this file · 1 suggestion to review in Settings → Disclosure.',
  );
  assert.equal(
    disclosureFactsSummary([{ status: 'suggested' }, { status: 'suggested' }]),
    '2 suggestions to review in Settings → Disclosure.',
  );
  assert.equal(disclosureFactsSummary([{ status: 'matches' }]), null);
  assert.equal(disclosureFactsSummary(), null);
});

test('sameFactValue compares a setting with what the file printed', () => {
  assert.equal(sameFactValue(350, 350), true);
  assert.equal(sameFactValue(0.02, 0.018), false);
  assert.equal(sameFactValue(null, 350), false);
  assert.equal(sameFactValue('  Smith  Reserve Co ', 'smith reserve co'), true);
  const brackets = [{ start_year: 2027, end_year: 2030, rate: 0.03 }];
  assert.equal(sameFactValue(structuredClone(brackets), brackets), true);
  assert.equal(sameFactValue([], brackets), false);
  // A loan matches on the fields the file printed; the operator's own details don't count.
  assert.equal(
    sameFactValue(
      { balance: 50000, lender: 'First Bank', interest_rate: 0.045 },
      { balance: 50000, lender: 'first bank', interest_rate: null },
    ),
    true,
  );
  assert.equal(sameFactValue(null, { balance: 50000, lender: null }), false);
});
