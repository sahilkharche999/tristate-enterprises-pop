/**
 * Unit tests for Edit-in-settings navigation helpers.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.MutationObserver = dom.window.MutationObserver;
globalThis.CSS = {
  escape: (s) =>
    String(s).replace(/[^a-zA-Z0-9_\u00A0-\uFFFF-]/g, (ch) =>
      `\\${ch.codePointAt(0).toString(16)} `,
    ),
};

const {
  resolveSettingsSection,
  safeReturnTo,
  resolveSettingsBackHref,
  buildSettingsEditHref,
  withSection,
  withRevealField,
  clearFieldParam,
  waitForSettingField,
  findSettingFieldEl,
  PROPERTY_SETTING_FIELD_ANCHORS,
  withOpenPackageLanguagePath,
  pathWantsOpenPackageLanguage,
  wantsOpenPackageLanguage,
  stripOpenPackageLanguageParam,
  OPEN_PACKAGE_LANGUAGE_PARAM,
} = await import('../src/app/lib/settingsNavigation.ts');

test('resolveSettingsSection: known and unknown', () => {
  assert.equal(resolveSettingsSection('disclosure'), 'disclosure');
  assert.equal(resolveSettingsSection('packages'), 'packages');
  assert.equal(resolveSettingsSection(null), 'database');
  assert.equal(resolveSettingsSection('nope'), 'database');
});

test('buildSettingsEditHref: database omits section', () => {
  const href = buildSettingsEditHref({
    hoaId: 10,
    tab: 'database',
    field: 'hoaName',
    returnTo: '/hoa/10/disclosure',
  });
  const url = new URL(href, 'http://local.test');
  assert.equal(url.pathname, '/hoa/10/settings');
  assert.equal(url.searchParams.get('field'), 'hoaName');
  assert.equal(url.searchParams.get('returnTo'), '/hoa/10/disclosure');
  assert.equal(url.searchParams.get('section'), null);
});

test('buildSettingsEditHref: disclosure sets section', () => {
  const href = buildSettingsEditHref({
    hoaId: 10,
    tab: 'disclosure',
    field: 'cpa_firm_name',
    returnTo: '/hoa/10/disclosure',
  });
  const url = new URL(href, 'http://local.test');
  assert.equal(url.searchParams.get('section'), 'disclosure');
  assert.equal(url.searchParams.get('field'), 'cpa_firm_name');
});

test('safeReturnTo: allow same HOA paths', () => {
  assert.equal(safeReturnTo('/hoa/10/disclosure', 10), '/hoa/10/disclosure');
  assert.equal(safeReturnTo('/hoa/10', 10), '/hoa/10');
  assert.equal(safeReturnTo('/hoa/10?view=enriched', 10), '/hoa/10?view=enriched');
  assert.equal(safeReturnTo('/hoa/10/settings', '10'), '/hoa/10/settings');
});

test('safeReturnTo: reject unsafe and cross-HOA', () => {
  assert.equal(safeReturnTo(null, 10), null);
  assert.equal(safeReturnTo('', 10), null);
  assert.equal(safeReturnTo('https://evil.com', 10), null);
  assert.equal(safeReturnTo('//evil.com', 10), null);
  assert.equal(safeReturnTo('/login', 10), null);
  assert.equal(safeReturnTo('/hoa/11/disclosure', 10), null);
  assert.equal(safeReturnTo('hoa/10/disclosure', 10), null);
  assert.equal(safeReturnTo('/hoa/10\\evil', 10), null);
});

test('resolveSettingsBackHref falls back to HOA home', () => {
  assert.equal(resolveSettingsBackHref('https://evil.com', 10), '/hoa/10');
  assert.equal(resolveSettingsBackHref('/hoa/10/disclosure', 10), '/hoa/10/disclosure');
});

test('withRevealField / withSection preserve returnTo', () => {
  const base = new URLSearchParams({ returnTo: '/hoa/10/disclosure' });
  const disc = withRevealField(base, 'disclosure', 'cpa_firm_name');
  assert.equal(disc.get('section'), 'disclosure');
  assert.equal(disc.get('field'), 'cpa_firm_name');
  assert.equal(disc.get('returnTo'), '/hoa/10/disclosure');

  const db = withRevealField(base, 'database', 'hoaName');
  assert.equal(db.get('section'), null);
  assert.equal(db.get('field'), 'hoaName');
  assert.equal(db.get('returnTo'), '/hoa/10/disclosure');

  const cleared = clearFieldParam(disc);
  assert.equal(cleared.get('field'), null);
  assert.equal(cleared.get('returnTo'), '/hoa/10/disclosure');
  assert.equal(cleared.get('section'), 'disclosure');

  const packages = withSection(base, 'packages');
  assert.equal(packages.get('section'), 'packages');
  assert.equal(packages.get('returnTo'), '/hoa/10/disclosure');
});

test('round-trip: build href returnTo is accepted by safeReturnTo', () => {
  const href = buildSettingsEditHref({
    hoaId: 7,
    tab: 'disclosure',
    field: 'letter_date',
    returnTo: '/hoa/7/disclosure',
  });
  const url = new URL(href, 'http://local.test');
  assert.equal(safeReturnTo(url.searchParams.get('returnTo'), 7), '/hoa/7/disclosure');
});

test('withOpenPackageLanguagePath appends reopen flag', () => {
  const path = withOpenPackageLanguagePath('/hoa/3/disclosure');
  assert.equal(path, `/hoa/3/disclosure?${OPEN_PACKAGE_LANGUAGE_PARAM}=1`);
  assert.equal(pathWantsOpenPackageLanguage(path), true);
  // idempotent
  assert.equal(withOpenPackageLanguagePath(path), path);
  // preserves existing query
  const withView = withOpenPackageLanguagePath('/hoa/3/disclosure?view=x');
  const u = new URL(withView, 'http://local.test');
  assert.equal(u.searchParams.get('view'), 'x');
  assert.equal(u.searchParams.get(OPEN_PACKAGE_LANGUAGE_PARAM), '1');
});

test('buildSettingsEditHref: package-language returnTo survives allowlist', () => {
  const returnTo = withOpenPackageLanguagePath('/hoa/3/disclosure');
  const href = buildSettingsEditHref({
    hoaId: 3,
    tab: 'database',
    field: 'city',
    returnTo,
  });
  const url = new URL(href, 'http://local.test');
  const rt = url.searchParams.get('returnTo');
  assert.equal(safeReturnTo(rt, 3), returnTo);
  assert.equal(pathWantsOpenPackageLanguage(rt), true);
});

test('stripOpenPackageLanguageParam / wantsOpenPackageLanguage', () => {
  const params = new URLSearchParams({
    [OPEN_PACKAGE_LANGUAGE_PARAM]: '1',
    other: 'x',
  });
  assert.equal(wantsOpenPackageLanguage(params), true);
  const stripped = stripOpenPackageLanguageParam(params);
  assert.equal(wantsOpenPackageLanguage(stripped), false);
  assert.equal(stripped.get('other'), 'x');
});

test('waitForSettingField: immediate hit', async () => {
  document.body.innerHTML = '<div data-setting-field="cpa_firm_name">CPA</div>';
  const el = await waitForSettingField('cpa_firm_name', { timeoutMs: 200 });
  assert.ok(el);
  assert.equal(el.getAttribute('data-setting-field'), 'cpa_firm_name');
});

test('waitForSettingField: late add via MutationObserver', async () => {
  document.body.innerHTML = '<div id="root"></div>';
  const pending = waitForSettingField('units', { timeoutMs: 1000 });
  setTimeout(() => {
    const node = document.createElement('div');
    node.setAttribute('data-setting-field', 'units');
    document.getElementById('root').appendChild(node);
  }, 30);
  const el = await pending;
  assert.ok(el);
  assert.equal(el.getAttribute('data-setting-field'), 'units');
});

test('waitForSettingField: timeout returns null', async () => {
  document.body.innerHTML = '';
  const el = await waitForSettingField('missing_field', { timeoutMs: 50 });
  assert.equal(el, null);
});

test('findSettingFieldEl uses CSS.escape-safe selector', () => {
  document.body.innerHTML = '<label data-setting-field="hoaName">Name</label>';
  assert.ok(findSettingFieldEl('hoaName'));
});

test('property anchors exist in SettingsScreen.tsx', () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const src = readFileSync(
    join(here, '../src/app/components/SettingsScreen.tsx'),
    'utf8',
  );
  for (const field of PROPERTY_SETTING_FIELD_ANCHORS) {
    assert.match(
      src,
      new RegExp(`data-setting-field="${field}"`),
      `missing data-setting-field="${field}" in SettingsScreen`,
    );
  }
});
