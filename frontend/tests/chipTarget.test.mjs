/**
 * Chip clicks resolve to the right catalog entry.
 *
 * A chip renders as its *name* ("CPA firm name"), so clicking it is how the
 * operator finds out what it will actually print and where to change it. The
 * click is caught by delegation on the editor container, which means the chip
 * has to be recovered from the DOM — this covers that recovery for both chip
 * shapes the schema emits: `<span data-var>` and the `<div|li data-block>`
 * carriers.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Element = dom.window.Element;
globalThis.HTMLElement = dom.window.HTMLElement;

const { resolveChipTarget } = await import(
  '../src/app/components/chipTarget.ts'
);

const VARIABLES = [
  { id: 'cpa_firm_name', label: 'CPA firm name' },
  { id: 'hoa_name', label: 'HOA name' },
];
const BLOCKS = [
  { id: 'special_assessment_disclosure', label: '§5300 disclosure' },
  { id: 'appendix_toc_rows', label: 'Appendix TOC rows' },
];

function render(html) {
  document.body.innerHTML = html;
  return document.body;
}

function click(selector) {
  return resolveChipTarget(document.querySelector(selector), VARIABLES, BLOCKS);
}

test('a value chip resolves to its catalog entry', () => {
  render('<p>From <span data-var="cpa_firm_name">CPA firm name</span>.</p>');
  const hit = click('[data-var]');
  assert.equal(hit.kind, 'value');
  assert.equal(hit.chip.label, 'CPA firm name');
});

test('clicking the label inside a chip still resolves the chip', () => {
  // The NodeView renders the label as a child node, so the click target is
  // usually a text node's parent, not the chip element itself.
  render('<p><span data-var="hoa_name"><em id="inner">HOA name</em></span></p>');
  const hit = click('#inner');
  assert.equal(hit.chip.id, 'hoa_name');
  assert.equal(hit.element.getAttribute('data-var'), 'hoa_name');
});

test('a div block carrier resolves as a block chip', () => {
  render('<div data-block="appendix_toc_rows"></div>');
  const hit = click('[data-block]');
  assert.equal(hit.kind, 'block');
  assert.equal(hit.chip.id, 'appendix_toc_rows');
});

test('an li block carrier resolves too', () => {
  // §5300 rides in an <li> so the whole bullet vanishes when it resolves
  // empty — the click path must handle that carrier as well as <div>.
  render('<ol><li data-block="special_assessment_disclosure"></li></ol>');
  const hit = click('li');
  assert.equal(hit.kind, 'block');
  assert.equal(hit.chip.id, 'special_assessment_disclosure');
});

test('ordinary prose resolves to nothing', () => {
  render('<p id="prose">Dear Homeowner:</p>');
  assert.equal(click('#prose'), null);
});

test('a chip missing from the catalog stays inert', () => {
  // Catalog drift between backend and page. An empty popover would be a worse
  // answer than no popover.
  render('<p><span data-var="chip_from_the_future"></span></p>');
  assert.equal(click('[data-var]'), null);
});

test('a non-element target resolves to nothing', () => {
  assert.equal(resolveChipTarget(null, VARIABLES, BLOCKS), null);
  assert.equal(resolveChipTarget(dom.window, VARIABLES, BLOCKS), null);
});

test('the nearest chip wins when prose sits between two of them', () => {
  render(
    '<p><span data-var="hoa_name">a</span>' +
      ' and <span data-var="cpa_firm_name"><b id="second">b</b></span></p>',
  );
  assert.equal(click('#second').chip.id, 'cpa_firm_name');
});
