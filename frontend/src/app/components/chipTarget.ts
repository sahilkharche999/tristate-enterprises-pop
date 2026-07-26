/**
 * Which chip did the operator just click?
 *
 * Kept JSX-free and separate from `NarrativeDocumentEditor` so it can be
 * tested directly (the project's test runner cannot compile JSX — see
 * `tests/narrativeRoundTrip.test.mjs` for the same split on the schema).
 *
 * Clicks are resolved by delegation on the editor container rather than inside
 * the chip NodeViews: one handler covers both chip kinds, and it does not rely
 * on React context reaching TipTap's node-view portals.
 */

export interface ChipCatalogEntry {
  id: string;
  label: string;
}

export interface ResolvedChip<T extends ChipCatalogEntry> {
  chip: T;
  kind: 'value' | 'block';
  element: HTMLElement;
}

/**
 * Walk up from a click target to the chip that contains it.
 *
 * Returns null for ordinary prose, and — deliberately — for a chip whose id is
 * absent from the catalogs. An unknown chip means the backend catalog and this
 * page have drifted; opening an empty popover would be a worse answer than
 * leaving the click inert.
 */
export function resolveChipTarget<T extends ChipCatalogEntry>(
  target: EventTarget | null,
  variables: readonly T[],
  blocks: readonly T[],
): ResolvedChip<T> | null {
  if (!(target instanceof Element)) return null;

  const element = target.closest<HTMLElement>('[data-var], [data-block]');
  if (!element) return null;

  // `data-var` is checked first: a value chip is never nested in a block
  // carrier, but a carrier's own attributes should not shadow it if that
  // ever changes.
  const isValue = element.hasAttribute('data-var');
  const id = isValue
    ? element.getAttribute('data-var')
    : element.getAttribute('data-block');
  if (!id) return null;

  const chip = (isValue ? variables : blocks).find((c) => c.id === id);
  if (!chip) return null;

  return { chip, kind: isValue ? 'value' : 'block', element };
}
