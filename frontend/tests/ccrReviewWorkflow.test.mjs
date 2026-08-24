import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  buildAdvancedFactorPayload,
  buildAdvancedCategoryPool,
  buildCCRCorrectionAction,
  buildCCRFactorPayload,
  buildCCRExtractedDetail,
  buildCCRReadySummary,
  buildIssueCard,
  displayCategoryName,
  friendlyAllocationMethod,
  friendlyAmountDisplay,
  friendlyPageType,
  friendlyWhoPays,
  mergeExtractionForDetail,
  ccrIssueIdentity,
  correctionActionLabel,
  executeCCRCorrection,
  friendlyCCRError,
  generateCategoryKey,
  isUsableCCRRecommendation,
  isCCRApprovalDisabled,
} from '../src/app/lib/ccrReviewWorkflow.ts';
import {
  getCCRPromotionPreview,
  parseFriendlyCCRApiError,
  saveCCRCorrectionOperation,
  saveCCRScalarCorrection,
} from '../src/app/api/ccr.ts';

const resolvedExtraction = {
  assessment_setup: { summary: 'Owners fund shared building expenses.' },
  allocation_pools: [
    {
      pool_key: 'operating',
      pool_name: 'Shared building expenses',
      annual_amount: '12000',
      allocation_method: 'equal',
      recipient_scope: 'all_units',
      pool_kind: 'recurring',
      source_pages: [4],
    },
  ],
};

test('advanced category drafts map friendly fields to domain values without exposing keys', () => {
  const key = generateCategoryKey('Roof & Exterior', new Set(['roof-exterior']));
  assert.equal(key, 'roof-exterior-2');
  assert.deepEqual(
    buildAdvancedCategoryPool(
      {
        name: 'Roof & Exterior',
        includedExpenses: 'Roof repairs, Exterior painting',
        billing: 'separate',
        cadence: 'one_time',
        amountAvailability: 'known',
        amount: '12500',
        recipientScope: 'all_units',
        participantUnitNumbers: [],
        allocation: 'fixed_amount',
        sourcePages: '8, 9',
      },
      key,
    ),
    {
      pool_key: 'roof-exterior-2',
      parent_pool_key: '',
      pool_name: 'Roof & Exterior',
      annual_amount: '12500',
      monthly_amount: null,
      allocation_method: 'specified_value',
      recipient_scope: 'all_units',
      selected_unit_numbers: [],
      denominator_label: '',
      denominator_value: null,
      denominator_source: 'unknown',
      included_budget_lines: ['Roof repairs', 'Exterior painting'],
      excluded_budget_lines: [],
      budget_line_derivation: 'explicit_lines',
      residual_after_pool_keys: [],
      residual_exclusions: [],
      source_pages: [8, 9],
      confidence: 1,
      allocation_context: 'special_assessment',
      billing_treatment: 'separate_one_time',
      billing_cadence: 'one_time',
      amount_availability: 'known',
      variable_flag: false,
      pool_kind: 'separately_billed_special_assessment',
    },
  );
});

test('advanced factors validate mixed proportional and per-home fixed requirements', () => {
  const result = buildAdvancedFactorPayload(
    [
      {
        unit_number: '101',
        square_feet: '1000',
        ownership_percent: '60',
        fixed_amounts: { capital: '1200' },
      },
      {
        unit_number: '102',
        square_feet: '900',
        ownership_percent: '40',
        fixed_amounts: { capital: '2400' },
      },
    ],
    2,
    {
      squareFeet: true,
      ownershipPercent: true,
      fixedCategoryKeys: ['capital'],
    },
  );

  assert.deepEqual(result, {
    values: [
      {
        unit_number: '101',
        square_feet: 1000,
        ownership_percent: 60,
        fixed_amounts: { capital: 1200 },
      },
      {
        unit_number: '102',
        square_feet: 900,
        ownership_percent: 40,
        fixed_amounts: { capital: 2400 },
      },
    ],
    error: null,
  });
  assert.match(
    buildAdvancedFactorPayload(
      [
        {
          unit_number: '101',
          square_feet: '1000',
          ownership_percent: '',
          fixed_amounts: { capital: '1200' },
        },
      ],
      1,
      {
        squareFeet: true,
        ownershipPercent: true,
        fixedCategoryKeys: ['capital'],
      },
    ).error,
    /ownership percentage/i,
  );
});

test('fixed factors accept a documented zero annual amount', () => {
  assert.deepEqual(
    buildAdvancedFactorPayload(
      [
        {
          unit_number: '201',
          square_feet: '',
          ownership_percent: '',
          fixed_amounts: { parking: '0' },
        },
        {
          unit_number: '202',
          square_feet: '',
          ownership_percent: '',
          fixed_amounts: { parking: '0.00' },
        },
      ],
      2,
      {
        squareFeet: false,
        ownershipPercent: false,
        fixedCategoryKeys: ['parking'],
      },
    ),
    {
      values: [
        { unit_number: '201', fixed_amounts: { parking: 0 } },
        { unit_number: '202', fixed_amounts: { parking: 0 } },
      ],
      error: null,
    },
  );
  assert.match(
    buildAdvancedFactorPayload(
      [
        {
          unit_number: '201',
          square_feet: '',
          ownership_percent: '',
          fixed_amounts: { parking: '' },
        },
      ],
      1,
      {
        squareFeet: false,
        ownershipPercent: false,
        fixedCategoryKeys: ['parking'],
      },
    ).error,
    /fixed annual amount/i,
  );
});

test('empty participant list still saves documented zero amounts', () => {
  assert.deepEqual(
    buildAdvancedFactorPayload(
      [
        {
          unit_number: '201',
          square_feet: '',
          ownership_percent: '',
          fixed_amounts: { parking: '0' },
        },
        {
          unit_number: '202',
          square_feet: '',
          ownership_percent: '',
          fixed_amounts: { parking: '0' },
        },
      ],
      2,
      {
        squareFeet: false,
        ownershipPercent: false,
        fixedCategoryKeys: ['parking'],
        fixedRecipientUnitNumbers: { parking: [] },
      },
    ),
    {
      values: [
        { unit_number: '201', fixed_amounts: { parking: 0 } },
        { unit_number: '202', fixed_amounts: { parking: 0 } },
      ],
      error: null,
    },
  );
});

test('fixed factors require amounts only from selected participating homes', () => {
  assert.deepEqual(
    buildAdvancedFactorPayload(
      [
        {
          unit_number: '101',
          square_feet: '',
          ownership_percent: '',
          fixed_amounts: { capital: '1200' },
        },
        {
          unit_number: '102',
          square_feet: '',
          ownership_percent: '',
          fixed_amounts: {},
        },
      ],
      2,
      {
        squareFeet: false,
        ownershipPercent: false,
        fixedCategoryKeys: ['capital'],
        fixedRecipientUnitNumbers: { capital: ['101'] },
      },
    ),
    {
      values: [
        { unit_number: '101', fixed_amounts: { capital: 1200 } },
        { unit_number: '102' },
      ],
      error: null,
    },
  );
});

test('proportional factors require values only from each category participant set', () => {
  assert.deepEqual(
    buildAdvancedFactorPayload(
      [
        {
          unit_number: '101',
          square_feet: '1000',
          ownership_percent: '',
          fixed_amounts: {},
        },
        {
          unit_number: '201',
          square_feet: '',
          ownership_percent: '40',
          fixed_amounts: {},
        },
      ],
      null,
      {
        squareFeet: true,
        ownershipPercent: true,
        squareFeetUnitNumbers: ['101'],
        ownershipPercentUnitNumbers: ['201'],
        fixedCategoryKeys: [],
      },
    ),
    {
      values: [
        { unit_number: '101', square_feet: 1000 },
        { unit_number: '201', ownership_percent: 40 },
      ],
      error: null,
    },
  );
});

test('issue cards explain owner impact, recommendation, and evidence plainly', () => {
  const card = buildIssueCard({
    code: 'CCR_POOL_SOURCE_MISSING',
    severity: 'error',
    category_key: 'operating',
    source_pages: [4, 8],
    explanation: "Category 'operating' has no source page citation.",
    recommended_operation: {
      operation: 'update',
      category_key: 'operating',
      changes: { source_pages: [4, 8] },
    },
    approval_blocked: true,
  }, resolvedExtraction);

  assert.equal(card.heading, 'Shared building expenses needs your attention');
  assert.match(card.whatHappened, /supporting pages/i);
  assert.match(card.ownerImpact, /owner charges/i);
  assert.match(card.recommendation, /confirm/i);
  assert.deepEqual(card.evidence, [
    { page: 4, label: 'View PDF page 4' },
    { page: 8, label: 'View PDF page 8' },
  ]);
});

test('recommended typed operations receive friendly action labels', () => {
  assert.equal(
    correctionActionLabel({ operation: 'add', category_key: 'reserve', pool: {} }),
    'Create reserve category',
  );
  assert.equal(
    correctionActionLabel({
      operation: 'update',
      category_key: 'operating',
      changes: { pool_kind: 'recurring' },
    }),
    'Keep with regular expenses',
  );
  assert.equal(
    correctionActionLabel({
      operation: 'update',
      category_key: 'special',
      changes: { pool_kind: 'separately_billed_special_assessment' },
    }),
    'Mark as separately billed',
  );
  assert.equal(
    correctionActionLabel({
      operation: 'update',
      category_key: 'operating',
      changes: { recipient_scope: 'all_units' },
    }),
    'Choose who pays',
  );
  assert.equal(
    correctionActionLabel({
      operation: 'update',
      category_key: 'operating',
      changes: { annual_amount: '15000' },
    }),
    'Enter the missing amount',
  );
});

test('correction builder pins structural operations to the preview version', () => {
  assert.deepEqual(
    buildCCRCorrectionAction(
      {
        operation: 'update',
        category_key: 'operating',
        changes: { pool_kind: 'recurring' },
      },
      7,
    ),
    {
      kind: 'operation',
      value: {
        operation: 'update',
        base_version: 7,
        category_key: 'operating',
        changes: { pool_kind: 'recurring' },
      },
    },
  );
  assert.equal(buildCCRCorrectionAction({ operation: 'add' }, 7), null);
});

test('placeholder and no-op recommendations are never executable', () => {
  assert.equal(
    isUsableCCRRecommendation({
      operation: 'update',
      category_key: 'operating',
      changes: {},
    }, resolvedExtraction),
    false,
  );
  assert.equal(
    isUsableCCRRecommendation({
      operation: 'update',
      category_key: 'operating',
      changes: { source_pages: [] },
    }, resolvedExtraction),
    false,
  );
  assert.equal(
    isUsableCCRRecommendation({
      operation: 'update',
      category_key: 'operating',
      changes: { recipient_scope: 'all_units' },
    }, resolvedExtraction),
    false,
  );
  assert.equal(
    isUsableCCRRecommendation({
      operation: 'add',
      category_key: 'reserve',
      pool: { pool_key: 'reserve' },
    }, resolvedExtraction),
    false,
  );
});

test('complete recommendations that change reviewed data remain executable', () => {
  assert.equal(
    isUsableCCRRecommendation({
      operation: 'update',
      category_key: 'operating',
      changes: { pool_kind: 'separately_billed_special_assessment' },
    }, resolvedExtraction),
    true,
  );
  assert.equal(
    isUsableCCRRecommendation({
      operation: 'add',
      category_key: 'reserve',
      pool: {
        pool_key: 'reserve',
        pool_name: 'Reserve contribution',
        allocation_method: 'equal',
        recipient_scope: 'all_units',
        source_pages: [12],
      },
    }, resolvedExtraction),
    true,
  );
});

test('successful corrections always return a freshly fetched resolved preview', async () => {
  const calls = [];
  const fresh = {
    extraction_run_id: 9,
    review_version: 2,
    resolved_extraction: resolvedExtraction,
    issues: [],
    approval_blocked: false,
  };
  const result = await executeCCRCorrection(
    { kind: 'scalar', fieldPath: 'assessment_setup.summary', value: 'Updated' },
    {
      saveOperation: async () => calls.push('operation'),
      saveScalar: async () => calls.push('scalar'),
      saveFactors: async () => calls.push('factors'),
      refetchPreview: async () => {
        calls.push('preview');
        return fresh;
      },
    },
  );

  assert.deepEqual(calls, ['scalar', 'preview']);
  assert.deepEqual(result, { status: 'refreshed', preview: fresh });
});

test('saved corrections report refresh failure without becoming mutation failures', async () => {
  const result = await executeCCRCorrection(
    { kind: 'scalar', fieldPath: 'assessment_setup.summary', value: 'Updated' },
    {
      saveOperation: async () => {},
      saveScalar: async () => {},
      saveFactors: async () => {},
      refetchPreview: async () => {
        throw new Error('offline');
      },
    },
  );
  assert.equal(result.status, 'saved_refresh_failed');
  assert.match(String(result.refreshError), /offline/);
});

test('factor payload requires the complete unique positive owner set and preserves both values', () => {
  const drafts = [
    {
      unit_number: '101',
      square_feet: '1000',
      ownership_percent: '60',
      fixed_amounts: { capital: '1200' },
    },
    {
      unit_number: '102',
      square_feet: '900',
      ownership_percent: '40',
    },
  ];
  assert.match(
    buildCCRFactorPayload(drafts.slice(0, 1), 2, 'ownership_percent').error,
    /all 2 homes/i,
  );
  assert.match(
    buildCCRFactorPayload(
      [drafts[0], { ...drafts[1], unit_number: '101' }],
      2,
      'ownership_percent',
    ).error,
    /different home identifier/i,
  );
  assert.match(
    buildCCRFactorPayload(
      [{ ...drafts[0], ownership_percent: '0' }, drafts[1]],
      2,
      'ownership_percent',
    ).error,
    /positive number/i,
  );
  assert.deepEqual(
    buildCCRFactorPayload(drafts, 2, 'ownership_percent'),
    {
      values: [
        {
          unit_number: '101',
          square_feet: 1000,
          ownership_percent: 60,
          fixed_amounts: { capital: 1200 },
        },
        {
          unit_number: '102',
          square_feet: 900,
          ownership_percent: 40,
        },
      ],
      error: null,
    },
  );
});

test('issue identities survive reorder and removal', () => {
  const operating = {
    code: 'CCR_POOL_SOURCE_MISSING',
    category_key: 'operating',
    source_pages: [],
  };
  const reserve = {
    code: 'CCR_POOL_SOURCE_MISSING',
    category_key: 'reserve',
    source_pages: [],
  };
  assert.equal(
    ccrIssueIdentity(operating),
    'CCR_POOL_SOURCE_MISSING:operating',
  );
  assert.equal(
    ccrIssueIdentity(reserve),
    'CCR_POOL_SOURCE_MISSING:reserve',
  );
  assert.equal(
    ccrIssueIdentity([operating, reserve][1]),
    ccrIssueIdentity([reserve][0]),
  );
});

test('Bob-facing labels hide pool names, $0 amounts, and raw page types', () => {
  assert.equal(displayCategoryName('Parking Cost Center Pool'), 'Parking Cost Center');
  assert.equal(displayCategoryName('Equal Base Operating Assessment Pool'), 'Equal Base Operating Assessment');
  assert.equal(displayCategoryName('Swimming Pools'), 'Swimming Pools');
  assert.equal(displayCategoryName('Community Pools'), 'Community Pools');
  assert.equal(friendlyAllocationMethod('custom_factor'), 'Divided using an external schedule');
  assert.equal(friendlyWhoPays('owners with appurtenant parking spaces'), 'Homes with parking');
  assert.notEqual(friendlyWhoPays('non-parking residents'), 'Homes with parking');
  assert.equal(friendlyWhoPays('non-parking residents'), 'The homes named in the document');
  assert.notEqual(friendlyWhoPays('visitor parking excluded'), 'Homes with parking');
  assert.equal(
    friendlyAmountDisplay(null, 'external_schedule'),
    'Uses the DRE / budget schedule',
  );
  assert.equal(friendlyAmountDisplay(0, 'known'), 'Amount is not in this document');
  assert.equal(
    friendlyPageType('assessment/allocation provisions'),
    'Assessment and allocation rules',
  );
});

test('mergeExtractionForDetail backfills HOA metadata when resolved document_metadata is empty', () => {
  const parsedMeta = {
    association_name: 'Sample Street Homeowners Association',
    document_title: 'Declaration of Restrictions',
    document_date: '2017-11-27',
    total_units: 9,
    source_pages: [16, 17],
  };

  const emptyObject = mergeExtractionForDetail(
    { assessment_setup: { summary: 'Regular assessments' }, document_metadata: {} },
    { document_metadata: parsedMeta },
  );
  assert.equal(
    buildCCRExtractedDetail(emptyObject).hoa.associationName,
    parsedMeta.association_name,
  );
  assert.equal(
    buildCCRExtractedDetail(emptyObject).hoa.documentTitle,
    parsedMeta.document_title,
  );

  const emptyDefaults = mergeExtractionForDetail(
    {
      document_metadata: {
        association_name: '',
        document_title: '',
        document_date: '',
        source_pages: [],
      },
    },
    { document_metadata: parsedMeta },
  );
  assert.equal(
    buildCCRExtractedDetail(emptyDefaults).hoa.associationName,
    parsedMeta.association_name,
  );

  const operatorOverride = mergeExtractionForDetail(
    { document_metadata: { association_name: 'Operator Corrected HOA' } },
    { document_metadata: parsedMeta },
  );
  assert.equal(
    buildCCRExtractedDetail(operatorOverride).hoa.associationName,
    'Operator Corrected HOA',
  );
});

test('extracted detail view model keeps run-19 categories and homes without $0 or Pool', () => {
  const nineHomeRoster = [
    ['201', '2202.0', '14.5'],
    ['202', '1308.0', '8.6'],
    ['203', '1526.0', '10.1'],
    ['204', '2599.0', '17.2'],
    ['301', '1465.0', '9.7'],
    ['302', '1462.0', '9.7'],
    ['401', '1560.0', '10.3'],
    ['402', '1457.0', '9.6'],
    ['403', '1557.0', '10.3'],
  ];
  const merged = mergeExtractionForDetail(
    {
      assessment_setup: {
        summary: 'Regular assessments are divided equally except DRE-prorated items.',
        requires_dre_for_future_years: true,
        source_pages: [16],
      },
      allocation_pools: [
        {
          pool_name: 'Equal Base Operating Assessment Pool',
          allocation_method: 'equal',
          recipient_scope: 'all_units',
          annual_amount: null,
          amount_availability: 'external_schedule',
          included_budget_lines: [],
          source_pages: [16],
        },
        {
          pool_name: 'DRE Prorated Operating Expenses',
          allocation_method: 'custom_factor',
          recipient_scope: 'all_units',
          included_budget_lines: ['insurance', 'gas', 'water'],
          amount_availability: 'external_schedule',
          source_pages: [16],
        },
        {
          pool_name: 'DRE Prorated Reserves',
          allocation_method: 'custom_factor',
          recipient_scope: 'all_units',
          included_budget_lines: ['roof', 'paint', 'water heaters'],
          amount_availability: 'external_schedule',
          source_pages: [16],
        },
        {
          pool_name: 'Special Assessment - Structural Common Area',
          allocation_method: 'square_footage',
          allocation_context: 'special_assessment',
          billing_cadence: 'one_time',
          amount_availability: 'operator_pending',
          included_budget_lines: ['structural Common Area'],
          source_pages: [16],
        },
        {
          pool_name: 'Parking Cost Center Pool',
          allocation_method: 'specified_value',
          recipient_scope: 'parking_users',
          amount_availability: 'external_schedule',
          included_budget_lines: ['parking space expenses'],
          source_pages: [16, 17],
        },
      ],
      unit_structure: {
        unit_count: 9,
        units: nineHomeRoster.map(([unit_number, square_feet, ownership_percent]) => ({
          unit_number,
          square_feet,
          ownership_percent,
        })),
      },
    },
    {
      document_metadata: {
        association_name: '131 Missouri Street Homeowners Association',
        document_title: 'Declaration of Restrictions',
        document_date: '2017-11-27',
        total_units: 9,
        source_pages: [16, 17],
      },
      page_inventory: [
        {
          page_number: 16,
          page_type: 'assessment/allocation provisions',
          notes: 'division of assessments',
        },
      ],
      human_review_questions: [
        {
          question: 'Please provide the DRE-reviewed operating budget proration schedule.',
          reason: 'The CC&R defers those numbers to the DRE budget.',
          source_pages: [16],
        },
      ],
    },
  );

  const detail = buildCCRExtractedDetail(merged);
  assert.equal(detail.hoa.associationName, '131 Missouri Street Homeowners Association');
  assert.equal(detail.categories.length, 5);
  assert.equal(detail.homes.length, 9);
  assert.deepEqual(
    detail.homes.map((home) => home.unitNumber),
    nineHomeRoster.map(([unit]) => unit),
  );
  assert.equal(detail.hoa.unitCount, '9');
  assert.equal(detail.categories[0].name, 'Equal Base Operating Assessment');
  assert.equal(detail.categories[4].name, 'Parking Cost Center');
  assert.equal(detail.categories[0].amount, 'Uses the DRE / budget schedule');
  assert.equal(detail.categories[3].cadence, 'One-time');
  assert.equal(detail.categories[4].whoPays, 'Homes with parking');
  assert.equal(detail.pages[0].pageType, 'Assessment and allocation rules');
  assert.match(JSON.stringify(detail), /insurance, gas, water/);
  assert.doesNotMatch(JSON.stringify(detail), /\$0|pool_key|multi_pool|coherence/i);
  assert.doesNotMatch(detail.categories.map((row) => row.name).join(' '), /\bPool\b/);
});

test('approval stays disabled for blockers and becomes ready with a plain summary', () => {
  assert.equal(isCCRApprovalDisabled({ approval_blocked: true, issues: [{}] }, false), true);
  assert.equal(isCCRApprovalDisabled({ approval_blocked: false, issues: [] }, false), false);

  const summary = buildCCRReadySummary(resolvedExtraction);
  assert.equal(summary.heading, 'Ready to approve');
  assert.match(summary.charged, /Shared building expenses.*\$12,000/);
  assert.match(summary.whoPays, /all owners/i);
  assert.match(summary.howDivided, /equally/i);
  assert.match(summary.whenBilled, /regular/i);
});

test('friendly errors and visible workflow copy never expose internal terminology', () => {
  const message = friendlyCCRError({
    status: 422,
    message: JSON.stringify({
      message: 'coherence failed for allocation_pools[0].recipient_scope residual pool_key',
      issues: [{ code: 'CCR_ALLOCATION_STRUCTURE_INCOHERENT' }],
    }),
  });
  assert.equal(message, 'We could not save that correction. Refresh the review and try again.');
  assert.equal(
    parseFriendlyCCRApiError({
      message: 'setup_type enum rejected at allocation_pools[0].pool_key',
    }),
    'We could not save that correction. Refresh the review and try again.',
  );

  const card = buildIssueCard({
    code: 'CCR_ALLOCATION_STRUCTURE_INCOHERENT',
    severity: 'error',
    category_key: 'operating',
    source_pages: [],
    explanation: 'residual pool coherence failed at allocation_pools[0].recipient_scope',
    recommended_operation: null,
    approval_blocked: true,
  }, resolvedExtraction);
  const visible = JSON.stringify(card).toLowerCase();
  for (const forbidden of ['pool', 'residual', 'coherence', 'category_key', 'recipient_scope', 'allocation_pools']) {
    assert.equal(visible.includes(forbidden), false, `visible copy leaked ${forbidden}`);
  }
});

test('CC&R API clients use preview and correction endpoints with typed bodies', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    return new Response(JSON.stringify({
      extraction_run_id: 3,
      review_version: 1,
      resolved_extraction: resolvedExtraction,
      issues: [],
      approval_blocked: false,
      edit_id: 1,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  try {
    await getCCRPromotionPreview(2, 3, 'fixed');
    await saveCCRCorrectionOperation(2, 3, {
      operation: 'update',
      base_version: 1,
      category_key: 'operating',
      changes: { pool_kind: 'recurring' },
    });
    await saveCCRScalarCorrection(2, 3, {
      field_path: 'assessment_setup.summary',
      old_value: '',
      new_value: 'Updated',
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.match(requests[0].url, /promotion-preview\?setup_type=fixed$/);
  assert.match(requests[1].url, /\/edits$/);
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    field_path: 'allocation_pools.$operation',
    new_value: {
      operation: 'update',
      base_version: 1,
      category_key: 'operating',
      changes: { pool_kind: 'recurring' },
    },
    reason: 'Guided CC&R correction',
  });
  assert.equal(JSON.parse(requests[2].options.body).field_path, 'assessment_setup.summary');
});

test('workbench delegates CC&R runs to the guided workflow and keeps PDF jumps', () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const workbench = readFileSync(
    join(here, '../src/app/components/DREReviewWorkbench.tsx'),
    'utf8',
  );
  const guided = readFileSync(
    join(here, '../src/app/components/CCRCorrectionWorkflow.tsx'),
    'utf8',
  );
  const extracted = readFileSync(
    join(here, '../src/app/components/CCRExtractedDetail.tsx'),
    'utf8',
  );

  assert.match(workbench, /isCCR[\s\S]*CCRCorrectionWorkflow/);
  assert.match(guided, /What needs attention/);
  assert.match(guided, /CCRExtractedDetail/);
  assert.match(extracted, /What this document already says/);
  assert.match(guided, /Ready to approve/);
  assert.match(guided, /jumpToPage/);
  assert.match(guided, /preview\.resolved_extraction/);
  assert.match(guided, /CCRAdvancedCorrections/);
  assert.doesNotMatch(guided, /Reviewed charges/);
});
