/**
 * React NodeViews for the narrative editor.
 *
 * The content model itself lives in `boilerplateEditorSchema.ts` — deliberately
 * JSX-free so `tests/narrativeRoundTrip.test.mjs` can import and verify it.
 * This file supplies only the views, which affect the live editing DOM and
 * never parse/serialize. `buildEditorExtensions` delegates to the same
 * `buildSchemaExtensions` the test uses, so there is exactly one definition of
 * what the editor can represent.
 */
import { ReactNodeViewRenderer, NodeViewWrapper } from '@tiptap/react';
import type { Extensions } from '@tiptap/core';
import {
  buildSchemaExtensions,
  type SchemaOptions,
} from './boilerplateEditorSchema';

export * from './boilerplateEditorSchema';

function VariableChipView({
  node,
  extension,
}: {
  node: { attrs: { name: string } };
  extension: { options: { labels: Record<string, string> } };
}) {
  const name = node.attrs.name;
  const label = extension.options.labels[name] || name;
  return (
    <NodeViewWrapper
      as="span"
      data-var={name}
      contentEditable={false}
      className="mx-0.5 inline-flex select-none items-center rounded bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-800"
    >
      {label}
    </NodeViewWrapper>
  );
}

function ComputedBlockView({
  node,
  extension,
}: {
  node: { attrs: { name: string; carrier: string } };
  extension: { options: { labels: Record<string, string> } };
}) {
  const { name, carrier } = node.attrs;
  const label = extension.options.labels[name] || name;
  return (
    // The carrier must match the schema's: a chip inside a list is an <li>,
    // and rendering a <div> there would be invalid markup in the editing DOM.
    <NodeViewWrapper
      as={carrier === 'li' ? 'li' : 'div'}
      data-block={name}
      contentEditable={false}
      className="my-2 select-none rounded border border-dashed border-amber-400 bg-amber-50 px-3 py-2 text-xs text-amber-900"
    >
      <span className="font-medium">{label}</span>
      <span className="ml-2 text-amber-700">
        filled in when the package is generated
      </span>
    </NodeViewWrapper>
  );
}

function ComputedPageView({
  node,
}: {
  node: { attrs: { label: string; pageCount: number | null } };
}) {
  const { label, pageCount } = node.attrs;
  return (
    <NodeViewWrapper
      as="div"
      contentEditable={false}
      className="my-6 select-none rounded-md border border-[#e5e5e5] bg-[#fafafa] px-4 py-3 text-sm text-[#525252]"
    >
      <div className="font-medium text-[#1a1a1a]">{label}</div>
      <div className="mt-0.5 text-xs text-[#737373]">
        Generated from your budget and reserve study
        {pageCount ? ` · about ${pageCount} page${pageCount === 1 ? '' : 's'}` : ''}
        {' · not editable'}
      </div>
    </NodeViewWrapper>
  );
}

/** The shipped extension list: the tested schema plus its React views. */
export function buildEditorExtensions(
  options: Omit<SchemaOptions, 'nodeViews'>,
): Extensions {
  return buildSchemaExtensions({
    ...options,
    nodeViews: {
      variable: ReactNodeViewRenderer(VariableChipView),
      computedBlock: ReactNodeViewRenderer(ComputedBlockView),
      computedPage: ReactNodeViewRenderer(ComputedPageView),
    },
  });
}
