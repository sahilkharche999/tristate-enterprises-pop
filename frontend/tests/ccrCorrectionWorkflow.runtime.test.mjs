import assert from 'node:assert/strict';
import { after, beforeEach, test } from 'node:test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { JSDOM } from 'jsdom';
import { createServer } from 'vite';

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: 'http://localhost/',
});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
Object.defineProperty(globalThis, 'navigator', {
  configurable: true,
  value: dom.window.navigator,
});
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.Node = dom.window.Node;
globalThis.Event = dom.window.Event;
globalThis.MouseEvent = dom.window.MouseEvent;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { default: React, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const vite = await createServer({
  root: frontendRoot,
  appType: 'custom',
  logLevel: 'silent',
  server: { middlewareMode: true },
});
const { CCRCorrectionWorkflow } = await vite.ssrLoadModule(
  '/src/app/components/CCRCorrectionWorkflow.tsx',
);
const { CCRAdvancedCorrections } = await vite.ssrLoadModule(
  '/src/app/components/CCRAdvancedCorrections.tsx',
);

let root;
let requests;

beforeEach(() => {
  if (root) {
    act(() => root.unmount());
  }
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root'));
  requests = [];
});

after(async () => {
  if (root) {
    act(() => root.unmount());
  }
  await vite.close();
  dom.window.close();
});

function detail() {
  return {
    review_status: 'pending',
    document_type: 'ccr',
  };
}

function renderWorkflow(overrides = {}) {
  return act(async () => {
    root.render(
      React.createElement(CCRCorrectionWorkflow, {
        hoaId: 2,
        runId: 3,
        detail: detail(),
        setupType: 'per_unit',
        onSetupTypeChange: () => {},
        onCompare: () => {},
        jumpToPage: () => {},
        onRunChanged: () => {},
        ...overrides,
      }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });
}

function findButton(label) {
  return [...document.querySelectorAll('button')].find(
    (button) => button.textContent.trim() === label,
  );
}

async function waitForText(text) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (document.body.textContent.includes(text)) return;
    await act(async () => {
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 5));
    });
  }
  assert.fail(`Timed out waiting for "${text}". Current text: ${document.body.textContent}`);
}

function setInput(input, value) {
  const prototype =
    input instanceof dom.window.HTMLSelectElement
      ? dom.window.HTMLSelectElement.prototype
      : input instanceof dom.window.HTMLTextAreaElement
        ? dom.window.HTMLTextAreaElement.prototype
        : dom.window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(
    prototype,
    'value',
  ).set;
  setter.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

function deferred() {
  let resolvePromise;
  let rejectPromise;
  const promise = new Promise((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return { promise, resolve: resolvePromise, reject: rejectPromise };
}

test('missing factors allow adding homes and refresh after saving', async () => {
  const blockingPreview = {
    extraction_run_id: 3,
    review_version: 0,
    resolved_extraction: {
      allocation_pools: [
        {
          pool_key: 'ownership',
          pool_name: 'Shared building costs',
          annual_amount: '12000',
          allocation_method: 'ownership_percentage',
          recipient_scope: 'all_units',
          source_pages: [12],
        },
      ],
      unit_structure: { units: [] },
    },
    issues: [
      {
        code: 'CCR_UNIT_FACTORS_MISSING',
        severity: 'error',
        category_key: 'ownership',
        source_pages: [12],
        explanation: 'Missing values',
        recommended_operation: null,
        approval_blocked: true,
      },
    ],
    approval_blocked: true,
  };
  const cleanPreview = {
    ...blockingPreview,
    resolved_extraction: {
      ...blockingPreview.resolved_extraction,
      unit_structure: {
        units: [{ unit_number: '101', ownership_percent: 0.6 }],
      },
    },
    issues: [],
    approval_blocked: false,
  };
  let previewReads = 0;
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (String(url).endsWith('/factors') && options.method === 'POST') {
      return new Response(
        JSON.stringify({ extraction_run_id: 3, factors_saved: 1 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    }
    previewReads += 1;
    return new Response(
      JSON.stringify(previewReads === 1 ? blockingPreview : cleanPreview),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  };

  await renderWorkflow();
  await waitForText('Enter the missing values for each home');

  const homeInput = document.querySelector('input[aria-label="Home identifier 1"]');
  const valueInput = document.querySelector('input[aria-label="Ownership percentage for home 1"]');
  assert.ok(homeInput, 'empty extraction should still render a home identifier input');
  assert.ok(valueInput, 'empty extraction should still render a factor value input');

  await act(async () => {
    const addHome = [...document.querySelectorAll('button')].find(
      (button) => button.textContent.trim() === 'Add another home',
    );
    assert.ok(addHome);
    addHome.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  const secondHomeInput = document.querySelector('input[aria-label="Home identifier 2"]');
  const secondValueInput = document.querySelector('input[aria-label="Ownership percentage for home 2"]');
  assert.ok(secondHomeInput, 'Bob can add another home row');
  assert.ok(secondValueInput, 'the added row includes its factor value');

  await act(async () => {
    setInput(homeInput, '101');
    setInput(valueInput, '60');
    setInput(secondHomeInput, '102');
    setInput(secondValueInput, '40');
  });
  const save = [...document.querySelectorAll('button')].find(
    (button) => button.textContent.trim() === 'Save home values',
  );
  assert.ok(save);
  await act(async () => {
    save.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });
  await waitForText('Ready to approve');

  const factorRequest = requests.find(
    ({ url, options }) => url.endsWith('/factors') && options.method === 'POST',
  );
  assert.ok(factorRequest);
  assert.deepEqual(JSON.parse(factorRequest.options.body), {
    factors: [
      { unit_number: '101', ownership_percent: 60 },
      { unit_number: '102', ownership_percent: 40 },
    ],
  });
  assert.equal(previewReads, 2);
});

test('placeholder recommendations render guidance without a fix button', async () => {
  const preview = {
    extraction_run_id: 3,
    review_version: 0,
    resolved_extraction: {
      allocation_pools: [
        {
          pool_key: 'operating',
          pool_name: 'Shared building costs',
          annual_amount: '12000',
          allocation_method: 'equal',
          recipient_scope: 'all_units',
          source_pages: [],
        },
      ],
      unit_structure: { units: [] },
    },
    issues: [
      {
        code: 'CCR_POOL_SOURCE_MISSING',
        severity: 'error',
        category_key: 'operating',
        source_pages: [],
        explanation: 'Missing source',
        recommended_operation: {
          operation: 'update',
          category_key: 'operating',
          changes: { source_pages: [] },
        },
        approval_blocked: true,
      },
      {
        code: 'CCR_ALLOCATION_STRUCTURE_INCOHERENT',
        severity: 'error',
        category_key: 'operating',
        source_pages: [],
        explanation: 'Incomplete',
        recommended_operation: {
          operation: 'update',
          category_key: 'operating',
          changes: {},
        },
        approval_blocked: true,
      },
    ],
    approval_blocked: true,
  };
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    return new Response(JSON.stringify(preview), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  await renderWorkflow();
  await waitForText('What needs attention');

  const buttonCopy = [...document.querySelectorAll('button')]
    .map((button) => button.textContent.trim())
    .join(' | ');
  assert.doesNotMatch(buttonCopy, /Confirm supporting pages|Apply recommended correction/);
  assert.match(document.body.textContent, /More details are needed/i);
  assert.equal(
    requests.some(({ options }) => options.method === 'POST'),
    false,
    'rendering placeholders must not submit a correction',
  );
});

test('factor save enforces the complete owner set and preserves existing values', async () => {
  const preview = {
    extraction_run_id: 3,
    review_version: 0,
    resolved_extraction: {
      allocation_pools: [
        {
          pool_key: 'ownership',
          pool_name: 'Shared building costs',
          annual_amount: '12000',
          allocation_method: 'ownership_percentage',
          recipient_scope: 'all_units',
          source_pages: [12],
        },
      ],
      unit_structure: {
        unit_count: 3,
        units: [
          {
            unit_number: '101',
            square_feet: 1000,
            ownership_percent: 60,
          },
          {
            unit_number: '102',
            square_feet: 900,
            ownership_percent: 40,
          },
        ],
      },
    },
    issues: [
      {
        code: 'CCR_UNIT_FACTORS_MISSING',
        severity: 'error',
        category_key: 'ownership',
        source_pages: [12],
        explanation: 'Missing values',
        recommended_operation: null,
        approval_blocked: true,
      },
    ],
    approval_blocked: true,
  };
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    return new Response(
      JSON.stringify(
        String(url).endsWith('/factors')
          ? { extraction_run_id: 3, factors_saved: 3 }
          : preview,
      ),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  };

  await renderWorkflow();
  await waitForText('Save home values');
  await act(async () => {
    findButton('Save home values').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  await waitForText('Enter all 3 homes');
  assert.equal(
    requests.filter(({ options }) => options.method === 'POST').length,
    0,
  );

  await act(async () => {
    findButton('Add another home').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  const thirdHome = document.querySelector('input[aria-label="Home identifier 3"]');
  const thirdValue = document.querySelector(
    'input[aria-label="Ownership percentage for home 3"]',
  );
  await act(async () => {
    setInput(thirdHome, '101');
    setInput(thirdValue, '20');
    findButton('Save home values').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  await waitForText('different home identifier');
  assert.equal(
    requests.filter(({ options }) => options.method === 'POST').length,
    0,
  );

  await act(async () => {
    setInput(thirdHome, '103');
    setInput(thirdValue, '0');
    findButton('Save home values').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  await waitForText('positive number');
  assert.equal(
    requests.filter(({ options }) => options.method === 'POST').length,
    0,
  );

  await act(async () => {
    setInput(thirdValue, '20');
    findButton('Save home values').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });
  const factorRequest = requests.find(
    ({ url, options }) => url.endsWith('/factors') && options.method === 'POST',
  );
  assert.ok(factorRequest);
  assert.deepEqual(JSON.parse(factorRequest.options.body), {
    factors: [
      {
        unit_number: '101',
        square_feet: 1000,
        ownership_percent: 60,
      },
      {
        unit_number: '102',
        square_feet: 900,
        ownership_percent: 40,
      },
      { unit_number: '103', ownership_percent: 20 },
    ],
  });
});

test('source-page drafts stay with their issue after reorder and removal', async () => {
  const issue = (categoryKey) => ({
    code: 'CCR_POOL_SOURCE_MISSING',
    severity: 'error',
    category_key: categoryKey,
    source_pages: [],
    explanation: 'Missing source',
    recommended_operation: {
      operation: 'update',
      category_key: categoryKey,
      changes: { source_pages: [] },
    },
    approval_blocked: true,
  });
  const category = (key) => ({
    pool_key: key,
    pool_name: `${key} costs`,
    annual_amount: '100',
    allocation_method: 'equal',
    recipient_scope: 'all_units',
    source_pages: [],
  });
  const initial = {
    extraction_run_id: 3,
    review_version: 0,
    resolved_extraction: {
      allocation_pools: [category('operating'), category('reserve')],
      unit_structure: { units: [] },
    },
    issues: [issue('operating'), issue('reserve')],
    approval_blocked: true,
  };
  const afterRemoval = {
    ...initial,
    review_version: 1,
    resolved_extraction: {
      ...initial.resolved_extraction,
      allocation_pools: [category('reserve')],
    },
    issues: [issue('reserve')],
  };
  let previewReads = 0;
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (options.method === 'POST') {
      return new Response(JSON.stringify({ edit_id: 1 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    previewReads += 1;
    return new Response(
      JSON.stringify(previewReads === 1 ? initial : afterRemoval),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  };

  await renderWorkflow();
  await waitForText('reserve costs needs your attention');
  const pageInputs = document.querySelectorAll('input[placeholder="For example: 4, 8"]');
  await act(async () => {
    setInput(pageInputs[0], '4');
    setInput(pageInputs[1], '9');
    findButton('Save PDF pages').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });
  await waitForText('1 decision before approval');
  const remaining = document.querySelector(
    'input[placeholder="For example: 4, 8"]',
  );
  assert.equal(remaining.value, '9');
});

test('saved correction with failed refresh offers refresh retry without reposting', async () => {
  const blocking = {
    extraction_run_id: 3,
    review_version: 0,
    resolved_extraction: {
      allocation_pools: [
        {
          pool_key: 'operating',
          pool_name: 'Shared costs',
          annual_amount: '100',
          allocation_method: 'equal',
          recipient_scope: 'all_units',
          source_pages: [],
        },
      ],
      unit_structure: { units: [] },
    },
    issues: [
      {
        code: 'CCR_POOL_SOURCE_MISSING',
        severity: 'error',
        category_key: 'operating',
        source_pages: [],
        explanation: 'Missing source',
        recommended_operation: null,
        approval_blocked: true,
      },
    ],
    approval_blocked: true,
  };
  const clean = { ...blocking, issues: [], approval_blocked: false };
  let previewReads = 0;
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (options.method === 'POST') {
      return new Response(JSON.stringify({ edit_id: 1 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    previewReads += 1;
    if (previewReads === 2) {
      return new Response('unavailable', { status: 503 });
    }
    return new Response(JSON.stringify(previewReads === 1 ? blocking : clean), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  await renderWorkflow();
  await waitForText('Save PDF pages');
  const pageInput = document.querySelector(
    'input[placeholder="For example: 4, 8"]',
  );
  await act(async () => {
    setInput(pageInput, '4');
    findButton('Save PDF pages').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });
  await waitForText('Correction saved. Refresh needed');
  assert.equal(findButton('Save PDF pages'), undefined);
  assert.ok(findButton('Retry refresh'));
  await act(async () => {
    findButton('Retry refresh').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });
  await waitForText('Ready to approve');
  assert.equal(
    requests.filter(({ options }) => options.method === 'POST').length,
    1,
  );
});

test('successful approval remains successful when refresh fails', async () => {
  const clean = {
    extraction_run_id: 3,
    review_version: 0,
    resolved_extraction: {
      allocation_pools: [
        {
          pool_key: 'operating',
          pool_name: 'Shared costs',
          annual_amount: '100',
          allocation_method: 'equal',
          recipient_scope: 'all_units',
          source_pages: [4],
        },
      ],
      unit_structure: { units: [] },
    },
    issues: [],
    approval_blocked: false,
  };
  let previewReads = 0;
  globalThis.confirm = () => true;
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (String(url).endsWith('/approve') && options.method === 'POST') {
      return new Response(
        JSON.stringify({
          extraction_run_id: 3,
          promoted_setup_id: 7,
          setup_type: 'per_unit',
          promoted_at: '2026-08-24T00:00:00Z',
          reviewed_by: 'bob@example.com',
          snapshot_counts: {},
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    }
    previewReads += 1;
    if (previewReads === 2) {
      return new Response('unavailable', { status: 503 });
    }
    return new Response(JSON.stringify(clean), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  await renderWorkflow();
  await waitForText('Approve owner charges');
  await act(async () => {
    findButton('Approve owner charges').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });
  await waitForText('Owner charges approved. Refresh needed');
  const approvedButton = findButton('Approved');
  assert.ok(approvedButton);
  assert.equal(approvedButton.disabled, true);
  assert.ok(findButton('Retry refresh'));
  assert.equal(
    requests.filter(({ url }) => url.endsWith('/approve')).length,
    1,
  );
});

test('failed setup-type reload never exposes the prior preview or actions', async () => {
  const prior = {
    extraction_run_id: 3,
    review_version: 0,
    resolved_extraction: {
      allocation_pools: [
        {
          pool_key: 'prior',
          pool_name: 'Prior setup charge',
          annual_amount: '100',
          allocation_method: 'equal',
          recipient_scope: 'all_units',
          source_pages: [4],
        },
      ],
      unit_structure: { units: [] },
    },
    issues: [],
    approval_blocked: false,
  };
  const failedReload = deferred();
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (String(url).includes('setup_type=fixed')) {
      return failedReload.promise;
    }
    return new Response(JSON.stringify(prior), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  await renderWorkflow({ setupType: 'per_unit' });
  await waitForText('Prior setup charge');
  assert.ok(findButton('Approve owner charges'));

  await renderWorkflow({ setupType: 'fixed' });
  assert.match(document.body.textContent, /Preparing the latest corrected review/i);
  assert.doesNotMatch(document.body.textContent, /Prior setup charge/);
  assert.equal(findButton('Approve owner charges'), undefined);
  await act(async () => {
    failedReload.resolve(new Response('unavailable', { status: 503 }));
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });
  await waitForText('We could not load this review');
  assert.doesNotMatch(document.body.textContent, /Prior setup charge/);
  assert.equal(findButton('Approve owner charges'), undefined);
  assert.equal(
    requests.some(({ options }) => options.method === 'POST'),
    false,
  );
});

test('superseded preview response cannot overwrite the matching identity', async () => {
  const preview = (name) => ({
    extraction_run_id: 3,
    review_version: 0,
    resolved_extraction: {
      allocation_pools: [
        {
          pool_key: name.toLowerCase().replaceAll(' ', '-'),
          pool_name: name,
          annual_amount: '100',
          allocation_method: 'equal',
          recipient_scope: 'all_units',
          source_pages: [4],
        },
      ],
      unit_structure: { units: [] },
    },
    issues: [],
    approval_blocked: false,
  });
  const staleGrouped = deferred();
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    const requestUrl = String(url);
    if (requestUrl.includes('setup_type=grouped')) return staleGrouped.promise;
    const body = requestUrl.includes('setup_type=fixed')
      ? preview('Fixed newest')
      : preview('Initial per-unit');
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  await renderWorkflow({ setupType: 'per_unit' });
  await waitForText('Initial per-unit');
  await renderWorkflow({ setupType: 'grouped' });
  assert.doesNotMatch(document.body.textContent, /Initial per-unit/);
  await renderWorkflow({ setupType: 'fixed' });
  await waitForText('Fixed newest');

  await act(async () => {
    staleGrouped.resolve(
      new Response(JSON.stringify(preview('Grouped stale')), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });
  assert.match(document.body.textContent, /Fixed newest/);
  assert.doesNotMatch(document.body.textContent, /Grouped stale/);
});

test('advanced corrections are collapsed, require a reason, show evidence, and add a friendly category', async () => {
  const category = {
    pool_key: 'operating',
    pool_name: 'Shared costs',
    annual_amount: '100',
    allocation_method: 'equal',
    recipient_scope: 'all_units',
    included_budget_lines: ['Insurance'],
    billing_cadence: 'recurring',
    amount_availability: 'known',
    source_pages: [4],
  };
  const initial = {
    extraction_run_id: 3,
    review_version: 0,
    resolved_extraction: {
      allocation_pools: [category],
      unit_structure: { unit_count: 2, units: [] },
    },
    issues: [],
    approval_blocked: false,
  };
  const added = {
    ...initial,
    review_version: 1,
    resolved_extraction: {
      ...initial.resolved_extraction,
      allocation_pools: [
        category,
        {
          ...category,
          pool_key: 'roof-exterior',
          pool_name: 'Roof & Exterior',
          annual_amount: '12500',
          included_budget_lines: ['Roof repairs', 'Exterior painting'],
          source_pages: [8, 9],
        },
      ],
    },
  };
  let previewReads = 0;
  const jumped = [];
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (options.method === 'POST') {
      return new Response(JSON.stringify({ edit_id: 1 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    previewReads += 1;
    return new Response(JSON.stringify(previewReads === 1 ? initial : added), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  await renderWorkflow({ jumpToPage: (page) => jumped.push(page) });
  await waitForText('Advanced corrections');
  const disclosure = document.querySelector('details');
  assert.ok(disclosure);
  assert.equal(disclosure.open, false);
  disclosure.open = true;

  await act(async () => {
    setInput(document.querySelector('[aria-label="Correction type"]'), 'add');
  });
  await waitForText('Save new category');
  await act(async () => {
    findButton('View PDF page 4').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  assert.deepEqual(jumped, [4]);

  await act(async () => {
    setInput(document.querySelector('[aria-label="Category name"]'), 'Roof & Exterior');
    setInput(
      document.querySelector('[aria-label="Included expenses"]'),
      'Roof repairs, Exterior painting',
    );
    setInput(document.querySelector('[aria-label="Annual amount"]'), '12500');
    setInput(document.querySelector('[aria-label="Supporting PDF pages"]'), '8, 9');
    findButton('Save new category').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  await waitForText('Tell us why you are making this correction');
  assert.equal(
    requests.filter(({ options }) => options.method === 'POST').length,
    0,
  );

  await act(async () => {
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'The roof assessment is stated separately on pages 8–9.',
    );
    findButton('Save new category').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });
  await waitForText('Roof & Exterior');
  const posted = JSON.parse(
    requests.find(({ options }) => options.method === 'POST').options.body,
  );
  assert.equal(posted.reason, 'The roof assessment is stated separately on pages 8–9.');
  assert.equal(posted.new_value.operation, 'add');
  assert.equal(posted.new_value.base_version, 0);
  assert.equal(posted.new_value.pool.pool_name, 'Roof & Exterior');
  assert.deepEqual(posted.new_value.pool.included_budget_lines, [
    'Roof repairs',
    'Exterior painting',
  ]);
  assert.match(posted.new_value.category_key, /^roof-exterior/);

  const visible = document.body.textContent.toLowerCase();
  for (const forbidden of [
    'pool_key',
    'category_key',
    'residual',
    'coherence',
    'field_path',
    'allocation_pools',
  ]) {
    assert.equal(visible.includes(forbidden), false, `visible copy leaked ${forbidden}`);
  }
});

function advancedPreview(categories, overrides = {}) {
  return {
    extraction_run_id: 3,
    review_version: 4,
    resolved_extraction: {
      allocation_pools: categories,
      unit_structure: { unit_count: 2, units: [] },
    },
    issues: [],
    approval_blocked: false,
    ...overrides,
  };
}

function category(key, name, overrides = {}) {
  return {
    pool_key: key,
    pool_name: name,
    annual_amount: '100',
    allocation_method: 'equal',
    recipient_scope: 'all_units',
    included_budget_lines: ['Insurance'],
    billing_cadence: 'recurring',
    amount_availability: 'known',
    source_pages: [4],
    ...overrides,
  };
}

function mockAdvancedSave(preview) {
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    return new Response(
      JSON.stringify(options.method === 'POST' ? { edit_id: 1 } : preview),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  };
}

function postedOperation() {
  const request = requests.find(({ options }) => options.method === 'POST');
  assert.ok(request);
  return JSON.parse(request.options.body);
}

test('advanced editor updates a category through a typed operation', async () => {
  mockAdvancedSave(advancedPreview([category('operating', 'Shared costs')]));
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;

  await waitForText('Save category changes');
  await act(async () => {
    setInput(document.querySelector('[aria-label="Category name"]'), 'Updated shared costs');
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'The document uses this category name.',
    );
    findButton('Save category changes').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });

  const posted = postedOperation();
  assert.equal(posted.new_value.operation, 'update');
  assert.equal(posted.new_value.base_version, 4);
  assert.equal(posted.new_value.category_key, 'operating');
  assert.equal(posted.new_value.changes.pool_name, 'Updated shared costs');
});

test('factor save refresh resets advanced rows when review version is unchanged', async () => {
  const pool = category('ownership', 'Shared costs', {
    allocation_method: 'square_footage',
  });
  const initial = advancedPreview([pool], {
    resolved_extraction: {
      allocation_pools: [pool],
      unit_structure: {
        unit_count: 1,
        units: [{ unit_number: '101', square_feet: 100 }],
      },
    },
  });
  const refreshed = {
    ...initial,
    resolved_extraction: {
      ...initial.resolved_extraction,
      unit_structure: {
        unit_count: 1,
        units: [{ unit_number: '101', square_feet: 300 }],
      },
    },
  };
  let reads = 0;
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (options.method === 'POST') {
      return new Response(JSON.stringify({ edit_id: 1, factors_saved: 1 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    reads += 1;
    return new Response(JSON.stringify(reads === 1 ? initial : refreshed), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;
  await act(async () => {
    setInput(
      document.querySelector('[aria-label="Square feet for advanced home 1"]'),
      '999',
    );
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'Save the reviewed home factor.',
    );
    findButton('Save category changes').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (
      document.querySelector('[aria-label="Square feet for advanced home 1"]')
        ?.value === '300'
    ) {
      break;
    }
    await act(async () => {
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 5));
    });
  }

  assert.equal(initial.review_version, refreshed.review_version);
  assert.equal(
    document.querySelector('[aria-label="Square feet for advanced home 1"]').value,
    '300',
  );
});

test('advanced editor splits a category with generated identifiers', async () => {
  mockAdvancedSave(advancedPreview([category('operating', 'Shared costs')]));
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;
  await act(async () => {
    setInput(document.querySelector('[aria-label="Correction type"]'), 'split');
  });
  await waitForText('Save split');

  const names = document.querySelectorAll('[aria-label="Category name"]');
  await act(async () => {
    setInput(names[0], 'Building expenses');
    setInput(names[1], 'Parking expenses');
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'The document assigns parking separately.',
    );
    findButton('Save split').dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });

  const posted = postedOperation();
  assert.equal(posted.new_value.operation, 'split');
  assert.equal(posted.new_value.category_key, 'operating');
  assert.deepEqual(
    posted.new_value.pools.map((pool) => pool.pool_name),
    ['Building expenses', 'Parking expenses'],
  );
  assert.equal(new Set(posted.new_value.pools.map((pool) => pool.pool_key)).size, 2);
});

test('advanced editor combines selected categories', async () => {
  mockAdvancedSave(
    advancedPreview([
      category('operating', 'Shared costs'),
      category('reserve', 'Reserve contribution', { source_pages: [7] }),
    ]),
  );
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;
  await act(async () => {
    setInput(document.querySelector('[aria-label="Correction type"]'), 'merge');
  });
  await waitForText('Save combined category');

  await act(async () => {
    for (const checkbox of document.querySelectorAll('input[type="checkbox"]')) {
      checkbox.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    }
    setInput(document.querySelector('[aria-label="Category name"]'), 'Combined shared costs');
    setInput(document.querySelector('[aria-label="Annual amount"]'), '200');
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'These are one charge in the document.',
    );
    findButton('Save combined category').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });

  const posted = postedOperation();
  assert.equal(posted.new_value.operation, 'merge');
  assert.deepEqual(posted.new_value.category_keys, ['operating', 'reserve']);
  assert.equal(posted.new_value.pool.pool_name, 'Combined shared costs');
});

test('advanced editor removes a category only after a reason', async () => {
  mockAdvancedSave(advancedPreview([category('operating', 'Shared costs')]));
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;
  await act(async () => {
    setInput(document.querySelector('[aria-label="Correction type"]'), 'remove');
  });
  await waitForText('Remove category');

  await act(async () => {
    findButton('Remove category').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  await waitForText('Tell us why you are making this correction');
  assert.equal(requests.some(({ options }) => options.method === 'POST'), false);

  await act(async () => {
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'This category was duplicated.',
    );
    findButton('Remove category').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });
  const posted = postedOperation();
  assert.equal(posted.new_value.operation, 'remove');
  assert.equal(posted.new_value.category_key, 'operating');
});

test('advanced allocation changes collect the complete owner set before either save', async () => {
  const preview = advancedPreview(
    [category('operating', 'Shared costs')],
    {
      resolved_extraction: {
        allocation_pools: [category('operating', 'Shared costs')],
        unit_structure: {
          unit_count: 2,
          units: [{ unit_number: '101', square_feet: 1000 }],
        },
      },
    },
  );
  mockAdvancedSave(preview);
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;

  await act(async () => {
    setInput(
      document.querySelector('[aria-label="How the charge is divided"]'),
      'square_footage',
    );
  });
  await waitForText('Enter the values for every home');
  await act(async () => {
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'The document divides this charge by home size.',
    );
    findButton('Save category changes').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  await waitForText('Enter all 2 homes');
  assert.equal(requests.some(({ options }) => options.method === 'POST'), false);

  await act(async () => {
    findButton('Add another home').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  const secondHome = document.querySelector(
    '[aria-label="Advanced home identifier 2"]',
  );
  const secondValue = document.querySelector(
    '[aria-label="Square feet for advanced home 2"]',
  );
  await act(async () => {
    setInput(secondHome, '102');
    setInput(secondValue, '900');
    findButton('Save category changes').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });

  const posts = requests.filter(({ options }) => options.method === 'POST');
  assert.equal(posts.length, 2);
  assert.match(posts[0].url, /\/edits$/);
  assert.match(posts[1].url, /\/factors$/);
  assert.deepEqual(JSON.parse(posts[1].options.body), {
    factors: [
      { unit_number: '101', square_feet: 1000 },
      { unit_number: '102', square_feet: 900 },
    ],
  });
});

test('blocking review decisions keep approval unavailable', async () => {
  const preview = advancedPreview([category('operating', 'Shared costs')], {
    issues: [
      {
        code: 'CCR_POOL_SOURCE_MISSING',
        severity: 'error',
        category_key: 'operating',
        source_pages: [],
        explanation: 'Missing evidence',
        recommended_operation: null,
        approval_blocked: true,
      },
    ],
    approval_blocked: true,
  });
  mockAdvancedSave(preview);
  await renderWorkflow();
  await waitForText('What needs attention');
  assert.equal(findButton('Approve owner charges').disabled, true);
});

test('advanced stale saves give friendly reload guidance without leaking internals', async () => {
  const preview = advancedPreview([category('operating', 'Shared costs')]);
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (options.method === 'POST') {
      return new Response(
        JSON.stringify({
          detail: {
            code: 'STALE_OPERATION_VERSION',
            message: 'The categories changed while this edit was open.',
          },
        }),
        { status: 409, headers: { 'Content-Type': 'application/json' } },
      );
    }
    return new Response(JSON.stringify(preview), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;
  await act(async () => {
    setInput(document.querySelector('[aria-label="Category name"]'), 'Updated costs');
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'The heading was corrected.',
    );
    findButton('Save category changes').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  await waitForText('This review changed while you were working');
  const text = document.body.textContent.toLowerCase();
  assert.doesNotMatch(text, /stale_operation_version|category_key|base_version/);
});

test('who-pays choices preserve a selected-home participant list', async () => {
  const preview = advancedPreview(
    [category('parking', 'Parking costs')],
    {
      resolved_extraction: {
        allocation_pools: [category('parking', 'Parking costs')],
        unit_structure: {
          unit_count: 3,
          units: [
            { unit_number: '101' },
            { unit_number: '102' },
            { unit_number: '103' },
          ],
        },
      },
    },
  );
  mockAdvancedSave(preview);
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;

  assert.equal(document.querySelector('input[aria-label="Who pays"]'), null);
  await act(async () => {
    setInput(document.querySelector('select[aria-label="Who pays"]'), 'custom_unit_list');
  });
  await waitForText('Choose every home that pays');
  await act(async () => {
    document.querySelector('[aria-label="Include home 101"]').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    document.querySelector('[aria-label="Include home 103"]').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'Only the documented parking homes pay.',
    );
    findButton('Save category changes').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });

  const poolChanges = postedOperation().new_value.changes;
  assert.equal(poolChanges.recipient_scope, 'custom_unit_list');
  assert.deepEqual(poolChanges.selected_unit_numbers, ['101', '103']);
  const posts = requests.filter(({ options }) => options.method === 'POST');
  assert.equal(posts.length, 2);
  assert.deepEqual(JSON.parse(posts[1].options.body).factors, [
    {
      unit_number: '101',
      square_feet: null,
      ownership_percent: null,
    },
    {
      unit_number: '102',
      square_feet: null,
      ownership_percent: null,
    },
    {
      unit_number: '103',
      square_feet: null,
      ownership_percent: null,
    },
  ]);
});

test('advanced editor rejects a missing or invalid known annual amount', async () => {
  mockAdvancedSave(advancedPreview([category('operating', 'Shared costs')]));
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;
  await act(async () => {
    setInput(document.querySelector('[aria-label="Correction type"]'), 'add');
  });
  await waitForText('Save new category');
  await act(async () => {
    setInput(document.querySelector('[aria-label="Category name"]'), 'Capital charge');
    setInput(document.querySelector('[aria-label="Supporting PDF pages"]'), '8');
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'The document identifies this charge.',
    );
    findButton('Save new category').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  await waitForText('positive annual amount');
  assert.equal(requests.some(({ options }) => options.method === 'POST'), false);
});

test('mixed categories collect both proportional factor columns', async () => {
  const pools = [
    category('area', 'Area costs', { allocation_method: 'square_footage' }),
    category('ownership', 'Ownership costs', {
      allocation_method: 'ownership_percentage',
    }),
  ];
  const preview = advancedPreview(pools, {
    resolved_extraction: {
      allocation_pools: pools,
      unit_structure: {
        unit_count: 2,
        units: [
          { unit_number: '101', square_feet: 1000, ownership_percent: 60 },
          { unit_number: '102', square_feet: 900, ownership_percent: 40 },
        ],
      },
    },
  });
  mockAdvancedSave(preview);
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;

  await waitForText('Enter the values for every home');
  assert.ok(
    document.querySelector('[aria-label="Square feet for advanced home 1"]'),
  );
  assert.ok(
    document.querySelector(
      '[aria-label="Ownership percentage for advanced home 1"]',
    ),
  );
});

test('fixed allocation saves complete per-home annual amounts', async () => {
  const preview = advancedPreview([category('operating', 'Shared costs')], {
    resolved_extraction: {
      allocation_pools: [category('operating', 'Shared costs')],
      unit_structure: {
        unit_count: 2,
        units: [{ unit_number: '101' }, { unit_number: '102' }],
      },
    },
  });
  mockAdvancedSave(preview);
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;
  await act(async () => {
    setInput(document.querySelector('[aria-label="Correction type"]'), 'add');
  });
  await waitForText('Save new category');
  await act(async () => {
    setInput(document.querySelector('[aria-label="Category name"]'), 'Capital contribution');
    setInput(document.querySelector('[aria-label="Annual amount"]'), '3600');
    setInput(
      document.querySelector('[aria-label="How the charge is divided"]'),
      'fixed_amount',
    );
    setInput(document.querySelector('[aria-label="Supporting PDF pages"]'), '9');
  });
  await waitForText('Enter the values for every home');
  await act(async () => {
    setInput(
      document.querySelector(
        '[aria-label="Fixed annual amount for Capital contribution, home 1"]',
      ),
      '1200',
    );
    setInput(
      document.querySelector(
        '[aria-label="Fixed annual amount for Capital contribution, home 2"]',
      ),
      '2400',
    );
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'The schedule lists a different fixed amount for each home.',
    );
    findButton('Save new category').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });

  const posts = requests.filter(({ options }) => options.method === 'POST');
  assert.equal(posts.length, 2);
  const operation = JSON.parse(posts[0].options.body).new_value;
  const factors = JSON.parse(posts[1].options.body).factors;
  assert.equal(operation.pool.allocation_method, 'specified_value');
  assert.deepEqual(factors, [
    {
      unit_number: '101',
      fixed_amounts: { [operation.category_key]: 1200 },
    },
    {
      unit_number: '102',
      fixed_amounts: { [operation.category_key]: 2400 },
    },
  ]);
});

test('external schedule allocation saves per-home category factors', async () => {
  const custom = category('operating-exceptions', 'Operating exceptions', {
    allocation_method: 'custom_factor',
    amount_availability: 'external_schedule',
  });
  const preview = advancedPreview([custom], {
    resolved_extraction: {
      allocation_pools: [custom],
      unit_structure: {
        unit_count: 2,
        units: [{ unit_number: '101' }, { unit_number: '102' }],
      },
    },
  });
  mockAdvancedSave(preview);
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;
  await waitForText('Enter the values for every home');

  const first = document.querySelector(
    '[aria-label="Custom factor for Operating exceptions, home 1"]',
  );
  const second = document.querySelector(
    '[aria-label="Custom factor for Operating exceptions, home 2"]',
  );
  assert.ok(first);
  assert.ok(second);
  await act(async () => {
    setInput(first, '14.5');
    setInput(second, '8.6');
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'The external schedule lists a factor for every home.',
    );
    findButton('Save category changes').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });

  const posts = requests.filter(({ options }) => options.method === 'POST');
  assert.equal(posts.length, 2);
  assert.deepEqual(JSON.parse(posts[1].options.body).factors, [
    {
      unit_number: '101',
      custom_factors: { 'operating-exceptions': 14.5 },
    },
    {
      unit_number: '102',
      custom_factors: { 'operating-exceptions': 8.6 },
    },
  ]);
});

test('named payer scopes derive evidenced participants and collect only their factors', async () => {
  const scoped = category('residential', 'Residential costs');
  const preview = advancedPreview([scoped], {
    resolved_extraction: {
      allocation_pools: [scoped],
      unit_structure: {
        unit_count: 2,
        units: [
          { unit_number: '101', category: 'residential', square_feet: 1000 },
          { unit_number: '201', category: 'commercial', square_feet: 1500 },
        ],
      },
    },
  });
  mockAdvancedSave(preview);
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;

  await act(async () => {
    setInput(
      document.querySelector('select[aria-label="Who pays"]'),
      'residential_only',
    );
    setInput(
      document.querySelector('[aria-label="How the charge is divided"]'),
      'square_footage',
    );
  });
  await waitForText('Choose every home that pays');
  assert.equal(
    document.querySelector('[aria-label="Include home 101"]').checked,
    true,
  );
  assert.equal(
    document.querySelector('[aria-label="Include home 201"]').checked,
    false,
  );
  assert.ok(
    document.querySelector('[aria-label="Square feet for advanced home 1"]'),
  );
  assert.equal(
    document.querySelector('[aria-label="Square feet for advanced home 2"]'),
    null,
  );

  await act(async () => {
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'The residential designation is shown beside home 101.',
    );
    findButton('Save category changes').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });

  const posts = requests.filter(({ options }) => options.method === 'POST');
  const operation = JSON.parse(posts[0].options.body).new_value;
  const factors = JSON.parse(posts[1].options.body).factors;
  assert.deepEqual(operation.changes.selected_unit_numbers, ['101']);
  assert.deepEqual(factors, [
    { unit_number: '101', square_feet: 1000 },
    { unit_number: '201' },
  ]);
});

test('known unit count blocks a partial equal-subset roster before either save', async () => {
  const preview = advancedPreview([category('operating', 'Shared costs')], {
    resolved_extraction: {
      allocation_pools: [category('operating', 'Shared costs')],
      unit_structure: { unit_count: 3, units: [] },
    },
  });
  mockAdvancedSave(preview);
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;

  await act(async () => {
    setInput(document.querySelector('[aria-label="Correction type"]'), 'add');
  });
  await waitForText('Save new category');
  await act(async () => {
    setInput(document.querySelector('[aria-label="Category name"]'), 'Parking homes');
    setInput(document.querySelector('[aria-label="Annual amount"]'), '1200');
    setInput(
      document.querySelector('select[aria-label="Who pays"]'),
      'custom_unit_list',
    );
  });
  await waitForText('Add another home');
  await act(async () => {
    findButton('Add another home').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  await act(async () => {
    setInput(
      document.querySelector('[aria-label="Advanced home identifier 1"]'),
      'P-101',
    );
  });
  await waitForText('Home P-101');
  await act(async () => {
    document.querySelector('[aria-label="Include home P-101"]').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    setInput(document.querySelector('[aria-label="Supporting PDF pages"]'), '12');
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'Only the identified parking home participates.',
    );
    findButton('Save new category').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  await waitForText('Enter all 3 homes before saving');
  assert.equal(requests.some(({ options }) => options.method === 'POST'), false);

  await act(async () => {
    for (let index = 0; index < 2; index += 1) {
      findButton('Add another home').dispatchEvent(
        new MouseEvent('click', { bubbles: true }),
      );
    }
  });
  await act(async () => {
    setInput(
      document.querySelector('[aria-label="Advanced home identifier 2"]'),
      'P-102',
    );
    setInput(
      document.querySelector('[aria-label="Advanced home identifier 3"]'),
      'P-103',
    );
    findButton('Save new category').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });

  const posts = requests.filter(({ options }) => options.method === 'POST');
  assert.equal(posts.length, 2);
  const operation = JSON.parse(posts[0].options.body).new_value;
  assert.deepEqual(operation.pool.selected_unit_numbers, ['P-101']);
  assert.deepEqual(JSON.parse(posts[1].options.body).factors, [
    {
      unit_number: 'P-101',
      square_feet: null,
      ownership_percent: null,
    },
    {
      unit_number: 'P-102',
      square_feet: null,
      ownership_percent: null,
    },
    {
      unit_number: 'P-103',
      square_feet: null,
      ownership_percent: null,
    },
  ]);
});

test('unknown unit count still permits manual subset rows and fails closed when empty', async () => {
  const preview = advancedPreview([category('operating', 'Shared costs')], {
    resolved_extraction: {
      allocation_pools: [category('operating', 'Shared costs')],
      unit_structure: { unit_count: null, units: [] },
    },
  });
  mockAdvancedSave(preview);
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;

  await act(async () => {
    setInput(
      document.querySelector('select[aria-label="Who pays"]'),
      'custom_unit_list',
    );
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'A manually identified home pays.',
    );
    findButton('Save category changes').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  await waitForText('Choose at least one home');
  assert.equal(requests.some(({ options }) => options.method === 'POST'), false);

  await act(async () => {
    findButton('Add another home').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
  });
  await act(async () => {
    setInput(
      document.querySelector('[aria-label="Advanced home identifier 1"]'),
      'Manual-1',
    );
  });
  await waitForText('Home Manual-1');
  await act(async () => {
    document.querySelector('[aria-label="Include home Manual-1"]').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    findButton('Save category changes').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });

  const operation = postedOperation().new_value;
  assert.deepEqual(operation.changes.selected_unit_numbers, ['Manual-1']);
  const posts = requests.filter(({ options }) => options.method === 'POST');
  assert.equal(posts.length, 2);
  assert.deepEqual(JSON.parse(posts[1].options.body).factors, [
    {
      unit_number: 'Manual-1',
      square_feet: null,
      ownership_percent: null,
    },
  ]);
});

test('advanced editor resets on factor-only preview revisions but preserves unrelated rerenders', async () => {
  const saves = [];
  const renderAdvanced = async (
    reviewVersion,
    previewRevision,
    poolName,
    unitNumber,
    squareFeet,
  ) => {
    await act(async () => {
      root.render(
        React.createElement(CCRAdvancedCorrections, {
          categories: [
            {
              ...category('operating', poolName),
              allocation_method: 'square_footage',
              recipient_scope: 'custom_unit_list',
              selected_unit_numbers: [unitNumber],
            },
          ],
          unitStructure: {
            unit_count: 1,
            units: [{ unit_number: unitNumber, square_feet: squareFeet }],
          },
          previewIdentity: 'run-3',
          previewRevision,
          reviewVersion,
          disabled: false,
          jumpToPage: () => {},
          onSave: async (...args) => {
            saves.push(args);
            return true;
          },
        }),
      );
    });
    document.querySelector('details').open = true;
  };

  await renderAdvanced(1, 1, 'Server v1', '101', 100);
  await act(async () => {
    setInput(document.querySelector('[aria-label="Category name"]'), 'Unsaved draft');
    setInput(
      document.querySelector('[aria-label="Square feet for advanced home 1"]'),
      '999',
    );
  });
  await renderAdvanced(1, 1, 'Same revision rerender', '101', 200);
  assert.equal(
    document.querySelector('[aria-label="Category name"]').value,
    'Unsaved draft',
  );
  assert.equal(
    document.querySelector('[aria-label="Square feet for advanced home 1"]').value,
    '999',
  );

  await renderAdvanced(1, 2, 'Server factor refresh', '202', 300);
  assert.equal(
    document.querySelector('[aria-label="Category name"]').value,
    'Server factor refresh',
  );
  assert.equal(
    document.querySelector('[aria-label="Advanced home identifier 1"]').value,
    '202',
  );
  assert.equal(
    document.querySelector('[aria-label="Square feet for advanced home 1"]').value,
    '300',
  );
  await act(async () => {
    setInput(
      document.querySelector('[aria-label="Reason for this correction"]'),
      'Save only the refreshed category.',
    );
    findButton('Save category changes').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });

  assert.equal(saves.length, 1);
  assert.equal(saves[0][0].base_version, 1);
  assert.equal(saves[0][0].changes.pool_name, 'Server factor refresh');
});

test('advanced billing choices expose only supported cadence', async () => {
  mockAdvancedSave(advancedPreview([category('operating', 'Shared costs')]));
  await renderWorkflow();
  await waitForText('Advanced corrections');
  document.querySelector('details').open = true;

  const billing = document.querySelector('[aria-label="How it is billed"]');
  const cadence = document.querySelector('[aria-label="Billing schedule"]');
  assert.ok(billing);
  assert.ok(cadence);
  assert.equal(cadence.value, 'recurring');
  assert.equal(cadence.disabled, true);

  await act(async () => setInput(billing, 'separate'));
  assert.equal(cadence.value, 'one_time');
  assert.equal(cadence.disabled, true);
  await act(async () => setInput(billing, 'regular'));
  assert.equal(cadence.value, 'recurring');
});

test('selected-home categories require the per-home setup choice', async () => {
  const selected = category('selected', 'Selected homes', {
    recipient_scope: 'custom_unit_list',
    selected_unit_numbers: ['101'],
  });
  mockAdvancedSave(advancedPreview([selected]));
  const changes = [];

  await renderWorkflow({
    setupType: 'fixed',
    onSetupTypeChange: (value) => changes.push(value),
  });
  await waitForText('How owner shares are organized');

  assert.deepEqual(changes, ['per_unit']);
  const setup = document.querySelector(
    '[aria-label="How owner shares are organized"]',
  );
  assert.equal(
    [...setup.options].find((option) => option.value === 'fixed').disabled,
    true,
  );
  assert.equal(
    [...setup.options].find((option) => option.value === 'grouped').disabled,
    true,
  );
});

test('all-home factor categories require the per-home setup choice', async () => {
  const factorBased = category('square', 'Square-foot share', {
    allocation_method: 'square_footage',
    recipient_scope: 'all_units',
  });
  mockAdvancedSave(advancedPreview([factorBased]));
  const changes = [];

  await renderWorkflow({
    setupType: 'grouped',
    onSetupTypeChange: (value) => changes.push(value),
  });
  await waitForText('How owner shares are organized');

  assert.deepEqual(changes, ['per_unit']);
  const setup = document.querySelector(
    '[aria-label="How owner shares are organized"]',
  );
  assert.equal(
    [...setup.options].find((option) => option.value === 'fixed').disabled,
    true,
  );
  assert.equal(
    [...setup.options].find((option) => option.value === 'grouped').disabled,
    true,
  );
});

test('guided custom-factor blocker saves category-specific factors and resolves', async () => {
  const blocked = {
    extraction_run_id: 3,
    review_version: 0,
    resolved_extraction: {
      allocation_pools: [
        category('custom', 'External schedule', {
          allocation_method: 'custom_factor',
        }),
      ],
      unit_structure: {
        unit_count: 2,
        units: [{ unit_number: '101' }, { unit_number: '102' }],
      },
    },
    issues: [
      {
        code: 'CCR_UNIT_FACTORS_MISSING',
        severity: 'error',
        category_key: 'custom',
        source_pages: [4],
        explanation: 'Missing custom factors',
        recommended_operation: null,
        approval_blocked: true,
      },
    ],
    approval_blocked: true,
  };
  let reads = 0;
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    if (String(url).endsWith('/factors') && options.method === 'POST') {
      return new Response(
        JSON.stringify({ extraction_run_id: 3, factors_saved: 2 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    }
    reads += 1;
    return new Response(
      JSON.stringify(
        reads === 1
          ? blocked
          : { ...blocked, issues: [], approval_blocked: false },
      ),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  };

  await renderWorkflow();
  await waitForText('Enter the missing values for each home');
  const first = document.querySelector(
    'input[aria-label="Custom factor for home 1"]',
  );
  const second = document.querySelector(
    'input[aria-label="Custom factor for home 2"]',
  );
  assert.ok(first);
  assert.ok(second);
  await act(async () => {
    setInput(first, '2');
    setInput(second, '3');
    findButton('Save home values').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });
  await waitForText('Ready to approve');

  const factorRequest = requests.find(
    ({ url, options }) => url.endsWith('/factors') && options.method === 'POST',
  );
  assert.deepEqual(JSON.parse(factorRequest.options.body), {
    factors: [
      { unit_number: '101', custom_factors: { custom: 2 } },
      { unit_number: '102', custom_factors: { custom: 3 } },
    ],
  });
});

test('guided specified-value blocker requires dollars only from participants', async () => {
  const preview = {
    extraction_run_id: 3,
    review_version: 0,
    resolved_extraction: {
      allocation_pools: [
        category('fixed', 'Selected documented amount', {
          allocation_method: 'specified_value',
          recipient_scope: 'custom_unit_list',
          selected_unit_numbers: ['101'],
        }),
      ],
      unit_structure: {
        unit_count: 2,
        units: [{ unit_number: '101' }, { unit_number: '102' }],
      },
    },
    issues: [
      {
        code: 'CCR_SPECIFIED_VALUES_MISSING',
        severity: 'error',
        category_key: 'fixed',
        source_pages: [5],
        explanation: 'Missing per-home dollars',
        recommended_operation: null,
        approval_blocked: true,
      },
    ],
    approval_blocked: true,
  };
  mockAdvancedSave(preview);

  await renderWorkflow();
  await waitForText('Fixed annual amount');
  await act(async () => {
    setInput(
      document.querySelector(
        'input[aria-label="Fixed annual amount for home 1"]',
      ),
      '1200',
    );
    findButton('Save home values').dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    );
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  });

  const factorRequest = requests.find(
    ({ url, options }) => url.endsWith('/factors') && options.method === 'POST',
  );
  assert.ok(factorRequest);
  assert.deepEqual(JSON.parse(factorRequest.options.body), {
    factors: [
      { unit_number: '101', fixed_amounts: { fixed: 1200 } },
      { unit_number: '102' },
    ],
  });
});

test('specified-value total mismatch stays editable in guided review', async () => {
  const preview = {
    extraction_run_id: 3,
    review_version: 0,
    resolved_extraction: {
      allocation_pools: [
        category('fixed', 'Documented amounts', {
          allocation_method: 'specified_value',
        }),
      ],
      unit_structure: {
        unit_count: 2,
        units: [
          {
            unit_number: '101',
            pool_factors: [
              {
                pool_key: 'fixed',
                factor_type: 'dollar_amount',
                factor_value: '1200',
              },
            ],
          },
          {
            unit_number: '102',
            pool_factors: [
              {
                pool_key: 'fixed',
                factor_type: 'dollar_amount',
                factor_value: '2300',
              },
            ],
          },
        ],
      },
    },
    issues: [
      {
        code: 'CCR_SPECIFIED_VALUES_INVALID',
        severity: 'error',
        category_key: 'fixed',
        source_pages: [5],
        explanation: 'Amounts do not reconcile',
        recommended_operation: null,
        approval_blocked: true,
      },
    ],
    approval_blocked: true,
  };
  mockAdvancedSave(preview);

  await renderWorkflow();
  await waitForText('do not add up');
  assert.ok(
    document.querySelector(
      'input[aria-label="Fixed annual amount for home 1"]',
    ),
  );
  assert.ok(
    document.querySelector(
      'input[aria-label="Fixed annual amount for home 2"]',
    ),
  );
});

test('legacy partial roster blocker asks for a full replacement', async () => {
  const base = advancedPreview([category('operating', 'Shared costs')]);
  const preview = {
    ...base,
    resolved_extraction: {
      ...base.resolved_extraction,
      unit_structure: { unit_count: 3, units: [{ unit_number: '101' }] },
    },
    issues: [
      {
        code: 'CCR_OPERATOR_ROSTER_INCOMPLETE',
        severity: 'error',
        category_key: null,
        source_pages: [],
        explanation: 'Saved list is incomplete',
        recommended_operation: null,
        approval_blocked: true,
      },
    ],
    approval_blocked: true,
  };
  mockAdvancedSave(preview);

  await renderWorkflow();
  await waitForText('Replace the complete home list');
  assert.match(document.body.textContent, /existing values were kept/i);
  assert.equal(findButton('Approve owner charges').disabled, true);
});

test('run-19 shaped preview shows extracted detail under guided cards without $0 or Pool', async () => {
  const homes = [
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
  const preview = {
    extraction_run_id: 19,
    review_version: 0,
    resolved_extraction: {
      assessment_setup: {
        summary:
          'Regular assessments are divided equally among all owners, except for insurance, gas, water, and reserves.',
        requires_dre_for_future_years: true,
        source_pages: [16, 17],
      },
      allocation_pools: [
        {
          pool_key: 'equal_base',
          pool_name: 'Equal Base Operating Assessment Pool',
          allocation_method: 'equal',
          recipient_scope: 'all_units',
          annual_amount: null,
          amount_availability: 'external_schedule',
          included_budget_lines: [],
          source_pages: [16],
        },
        {
          pool_key: 'dre_prorated_operating_expenses',
          pool_name: 'DRE Prorated Operating Expenses',
          allocation_method: 'custom_factor',
          recipient_scope: 'all_units',
          amount_availability: 'external_schedule',
          included_budget_lines: ['insurance', 'gas', 'water'],
          source_pages: [16],
        },
        {
          pool_key: 'dre_prorated_reserve_expenses',
          pool_name: 'DRE Prorated Reserves',
          allocation_method: 'custom_factor',
          recipient_scope: 'all_units',
          amount_availability: 'external_schedule',
          included_budget_lines: ['reserves for the roof', 'paint', 'water heaters'],
          source_pages: [16],
        },
        {
          pool_key: 'special_assessment_structural_sqft',
          pool_name: 'Special Assessment - Structural Common Area',
          allocation_method: 'square_footage',
          allocation_context: 'special_assessment',
          billing_cadence: 'one_time',
          amount_availability: 'operator_pending',
          included_budget_lines: ['structural Common Area'],
          source_pages: [16],
        },
        {
          pool_key: 'parking_cost_center',
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
        units: homes.map(([unit_number, square_feet, ownership_percent]) => ({
          unit_number,
          square_feet,
          ownership_percent,
        })),
      },
    },
    issues: [
      {
        code: 'CCR_UNIT_FACTORS_MISSING',
        severity: 'error',
        category_key: 'dre_prorated_operating_expenses',
        source_pages: [16],
        explanation: 'Missing DRE factors',
        recommended_operation: null,
        approval_blocked: true,
      },
    ],
    approval_blocked: true,
  };
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    return new Response(JSON.stringify(preview), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  await renderWorkflow({
    detail: {
      ...detail(),
      parsed_json: {
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
            reason: 'Those numbers live in the DRE budget.',
            source_pages: [16],
          },
        ],
      },
    },
  });

  await waitForText('What needs attention');
  const body = document.body.textContent;
  const attentionAt = body.indexOf('What needs attention');
  const extractedAt = body.indexOf('What this document already says');
  const advancedAt = body.indexOf('Advanced corrections');
  assert.ok(attentionAt >= 0 && extractedAt > attentionAt && advancedAt > extractedAt);

  assert.match(body, /131 Missouri Street Homeowners Association/);
  assert.match(body, /Regular assessments are divided equally/);
  assert.match(body, /Needs the yearly budget \/ DRE schedule/);
  assert.match(body, /Equal Base Operating Assessment/);
  assert.match(body, /DRE Prorated Operating Expenses/);
  assert.match(body, /DRE Prorated Reserves/);
  assert.match(body, /Special Assessment - Structural Common Area/);
  assert.match(body, /Parking Cost Center/);
  assert.match(body, /Divided equally/);
  assert.match(body, /Homes with parking/);
  assert.match(body, /Amount is not in this document|Uses the DRE \/ budget schedule/);
  assert.match(body, /Assessment and allocation rules/);
  assert.match(body, /Home 201|201/);
  assert.match(body, /403/);
  assert.doesNotMatch(body, /\$0 per year/);
  assert.doesNotMatch(body, /Reviewed charges/);
  assert.doesNotMatch(body, /multi_pool_combination|pool_key|coherence|residual/);
  assert.equal(findButton('Approve owner charges').disabled, true);

  const extractedHeading = [...document.querySelectorAll('h4')].map(
    (node) => node.textContent,
  );
  assert.ok(extractedHeading.includes('Equal Base Operating Assessment'));
  assert.ok(extractedHeading.includes('Parking Cost Center'));
  assert.ok(!extractedHeading.some((name) => /\bPool\b/.test(name)));
});
