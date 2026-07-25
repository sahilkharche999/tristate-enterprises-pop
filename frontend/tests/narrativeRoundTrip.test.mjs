/**
 * The editor must not destroy narrative content it cannot represent.
 *
 * ProseMirror silently drops anything outside its schema. For a disclosure
 * package that means opening a document and pressing Save — with no edits —
 * can strip headings, table structure, or the CSS classes the print stylesheet
 * depends on, in a legal document, with no error anywhere.
 *
 * The backend has an equivalent guard (`test_narrative_baselines.py` asserts
 * every baseline survives nh3). That one tests `baseline -> nh3 -> baseline`.
 * This one tests the boundary that actually reaches the operator:
 * `baseline -> TipTap -> baseline`. Both are needed; the backend test passing
 * says nothing about this one.
 *
 * Baselines are read from the backend source of truth so the two can't drift.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { JSDOM } from 'jsdom';

// TipTap's HTML helpers parse and serialize through the real DOM, so a DOM has
// to exist before @tiptap/core is imported.
const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.DOMParser = dom.window.DOMParser;
globalThis.XMLSerializer = dom.window.XMLSerializer;
globalThis.Node = dom.window.Node;
globalThis.Element = dom.window.Element;
globalThis.HTMLElement = dom.window.HTMLElement;

const { generateHTML, generateJSON } = await import('@tiptap/core');
const { buildSchemaExtensions } = await import(
  '../src/app/components/boilerplateEditorSchema.ts'
);

const HERE = dirname(fileURLToPath(import.meta.url));
const CONTENT_DIR = join(
  HERE,
  '..',
  '..',
  'backend',
  'app',
  'disclosure_package',
  'content',
  'standard',
);

const extensions = buildSchemaExtensions({
  variableLabels: {},
  blockLabels: {},
  withSections: false,
});

function roundTrip(html) {
  return generateHTML(generateJSON(html, extensions), extensions);
}

/** Collapse formatting-only whitespace so indentation isn't under test. */
function normalize(html) {
  return html.replace(/>\s+</g, '><').replace(/\s+/g, ' ').trim();
}

const baselines = readdirSync(CONTENT_DIR)
  .filter((name) => name.endsWith('.html'))
  .map((name) => [name, readFileSync(join(CONTENT_DIR, name), 'utf8').trim()]);

test('the backend ships baselines for this test to check', () => {
  assert.equal(baselines.length, 14);
});

for (const [name, html] of baselines) {
  test(`${name} survives a round trip through the editor schema`, () => {
    assert.equal(normalize(roundTrip(html)), normalize(html));
  });
}

// ── the specific losses that motivated this file ───────────────────────────

test('block-level classes survive', () => {
  const html = '<p class="legal-block">Text</p><p class="muted">More</p>';
  assert.equal(normalize(roundTrip(html)), normalize(html));
});

test('inline span classes survive', () => {
  const html = '<p>Funded <span class="bold">41.8%</span> today</p>';
  assert.equal(normalize(roundTrip(html)), normalize(html));
});

test('generic div wrappers survive', () => {
  const html = '<div class="letter-meta"><p>From: Board</p></div>';
  assert.equal(normalize(roundTrip(html)), normalize(html));
});

test('list classes and totals-row survive', () => {
  const html =
    '<ol class="disclosure-list"><li>One</li></ol>' +
    '<table><tbody><tr class="totals-row"><td>Total</td></tr></tbody></table>';
  assert.ok(normalize(roundTrip(html)).includes('class="disclosure-list"'));
  assert.ok(normalize(roundTrip(html)).includes('class="totals-row"'));
});

test('a value chip inside a table cell survives', () => {
  const html =
    '<table><tbody><tr><td>Percent funded</td>' +
    '<td><span data-var="percent_funded"></span></td></tr></tbody></table>';
  assert.ok(roundTrip(html).includes('data-var="percent_funded"'));
});

test('a chip span is never captured by the generic span rule', () => {
  const out = roundTrip('<p><span data-var="hoa_name"></span></p>');
  assert.ok(out.includes('data-var="hoa_name"'));
  assert.ok(!out.includes('class='));
});

test('an li-carried block chip stays inside its list', () => {
  const html =
    '<ol class="disclosure-list">' +
    '<li data-block="special_assessment_disclosure"></li>' +
    '<li>4950(b): minutes.</li></ol>';
  const out = roundTrip(html);
  // The carrier must still be an <li>, still inside the <ol>.
  assert.ok(out.includes('<li data-block="special_assessment_disclosure">'));
  assert.ok(out.indexOf('data-block') > out.indexOf('<ol'));
  assert.ok(out.indexOf('data-block') < out.indexOf('</ol>'));
});

test('a div-carried block chip stays a div', () => {
  const out = roundTrip('<div data-block="outstanding_loan_note"></div>');
  assert.ok(out.includes('<div data-block="outstanding_loan_note">'));
});

test('indent classes are not duplicated on round trip', () => {
  const out = roundTrip('<p class="indent-2">Indented</p>');
  assert.equal((out.match(/indent-2/g) || []).length, 1);
});

test('an indent class coexists with a styling class', () => {
  const out = roundTrip('<p class="legal-block indent-1">Both</p>');
  assert.ok(out.includes('legal-block'));
  assert.ok(out.includes('indent-1'));
});

// ── the top level stays locked in full-document mode ───────────────────────

test('sectioned mode rejects loose blocks at the top level', () => {
  const sectioned = buildSchemaExtensions({
    variableLabels: {},
    blockLabels: {},
    withSections: true,
  });
  const json = generateJSON(
    '<section data-doc-id="note_7"><p>Body</p></section><p>Loose paragraph</p>',
    sectioned,
  );
  // Everything at the top level is a section or a computed-page card; the
  // stray paragraph cannot survive there.
  assert.ok(json.content.every((n) => ['docSection', 'computedPage'].includes(n.type)));
});

test('sectioned mode preserves the doc id', () => {
  const sectioned = buildSchemaExtensions({
    variableLabels: {},
    blockLabels: {},
    withSections: true,
  });
  const out = generateHTML(
    generateJSON('<section data-doc-id="note_7"><p>Body</p></section>', sectioned),
    sectioned,
  );
  assert.ok(out.includes('data-doc-id="note_7"'));
});
