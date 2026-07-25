/**
 * The editor's content model — schema only, deliberately free of JSX.
 *
 * This file is the counterpart to `boilerplate_sanitize.CONTENT_MODEL_TAGS` on
 * the backend: between them they define what HTML a narrative disclosure
 * document may contain. They must agree. A tag or attribute the baselines use
 * but this schema omits is *silently deleted* the first time an operator opens
 * a document and saves it — ProseMirror drops what it cannot represent, with no
 * error, in a legal document.
 *
 * `tests/narrativeRoundTrip.test.mjs` parses every shipped baseline through this
 * schema and asserts equality, which is what makes that failure mode loud. The
 * test can only import this file because it contains no JSX — the React
 * NodeViews live in `boilerplateEditorExtensions.tsx` and are attached via
 * `withNodeViews()`. NodeViews affect only the live editing DOM, never
 * parse/serialize, so testing this list tests what actually ships.
 */
import { Extension, Mark, Node, mergeAttributes } from '@tiptap/core';
import type { Extensions } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table';
import { Document } from '@tiptap/extension-document';
import { BulletList, OrderedList } from '@tiptap/extension-list';

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    variableChip: {
      insertVariable: (name: string) => ReturnType;
    };
    computedBlockChip: {
      insertComputedBlock: (name: string) => ReturnType;
    };
    indent: {
      indent: () => ReturnType;
      outdent: () => ReturnType;
    };
  }
}

/** Tags whose `class` the baselines rely on for print styling. */
const CLASS_BEARING_TYPES = [
  'paragraph',
  'heading',
  'bulletList',
  'orderedList',
  'listItem',
  'table',
  'tableRow',
  'tableCell',
  'tableHeader',
];

const INDENT_CLASS_RE = /\bindent-\d\b/g;

/** Strip the tokens `Indent` owns so the two attributes don't duplicate them. */
function classWithoutIndent(element: HTMLElement): string | null {
  const raw = element.getAttribute('class');
  if (!raw) return null;
  const rest = raw.replace(INDENT_CLASS_RE, '').replace(/\s+/g, ' ').trim();
  return rest || null;
}

export interface VariableOptions {
  labels: Record<string, string>;
}

/**
 * `Variable`: an atomic inline node serializing to `<span data-var="NAME">` —
 * the exact token format `boilerplate_variables.resolve` (backend) parses.
 *
 * Its parse priority sits above `ClassSpan` so a chip is never swallowed by the
 * generic span rule.
 */
export const Variable = Node.create<VariableOptions>({
  name: 'variable',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  priority: 200,

  addOptions() {
    return { labels: {} };
  },

  addAttributes() {
    return {
      name: {
        default: null,
        parseHTML: (element) => (element as HTMLElement).getAttribute('data-var'),
        renderHTML: (attributes) => ({ 'data-var': attributes.name }),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'span[data-var]', priority: 100 }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['span', mergeAttributes(HTMLAttributes)];
  },

  addCommands() {
    return {
      insertVariable:
        (name: string) =>
        ({ chain }) =>
          chain().insertContent({ type: this.name, attrs: { name } }).run(),
    };
  },
});

export interface ComputedBlockOptions {
  labels: Record<string, string>;
}

/**
 * `ComputedBlock`: the block-level counterpart to `Variable`, serializing to
 * `<div data-block="NAME">` or `<li data-block="NAME">` — the two carriers
 * `boilerplate_variables.BLOCK_CARRIER_RE` (backend) parses. It holds content
 * the operator cannot author: multi-paragraph conditional/legal wording (the
 * §5300 disclosure, Note 8's loan paragraph) and loop-generated tables.
 *
 * `carrier` round-trips rather than being hardcoded because an `li` chip must
 * stay an `li` — the backend replaces the whole carrier element on resolution,
 * and a `div` inside a `<ul>` would be invalid markup. The list nodes below
 * widen their content expressions to admit this node for the same reason.
 *
 * Deliberately deletable: Bob may legitimately restructure a document. The
 * guarantee that statutory blocks survive lives in the backend's required-block
 * preflight, not in this schema.
 */
export const ComputedBlock = Node.create<ComputedBlockOptions>({
  name: 'computedBlock',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: false,
  isolating: true,
  priority: 200,

  addOptions() {
    return { labels: {} };
  },

  addAttributes() {
    return {
      name: {
        default: null,
        parseHTML: (element) => (element as HTMLElement).getAttribute('data-block'),
        renderHTML: (attributes) => ({ 'data-block': attributes.name }),
      },
      carrier: {
        default: 'div',
        parseHTML: (element) => (element as HTMLElement).tagName.toLowerCase(),
        // Structural only — never emitted as an attribute.
        renderHTML: () => ({}),
      },
    };
  },

  parseHTML() {
    return [
      { tag: 'div[data-block]', priority: 100 },
      { tag: 'li[data-block]', priority: 100 },
    ];
  },

  renderHTML({ node, HTMLAttributes }) {
    const carrier = (node.attrs as { carrier?: string }).carrier === 'li' ? 'li' : 'div';
    return [carrier, mergeAttributes(HTMLAttributes)];
  },

  addCommands() {
    return {
      insertComputedBlock:
        (name: string) =>
        ({ chain }) =>
          chain().insertContent({ type: this.name, attrs: { name, carrier: 'div' } }).run(),
    };
  },
});

const BLOCK_TAGS = new Set([
  'ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'DIV', 'DL', 'FIELDSET',
  'FIGURE', 'FOOTER', 'FORM', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HEADER',
  'HR', 'LI', 'MAIN', 'NAV', 'OL', 'P', 'PRE', 'SECTION', 'TABLE', 'UL',
]);

function hasBlockChild(element: HTMLElement): boolean {
  return Array.from(element.children).some((child) =>
    BLOCK_TAGS.has(child.tagName),
  );
}

const classAttribute = {
  class: {
    default: null as string | null,
    parseHTML: (element: HTMLElement) => element.getAttribute('class'),
    renderHTML: (attributes: Record<string, any>) =>
      attributes.class ? { class: attributes.class } : {},
  },
};

/**
 * `Div` / `InlineDiv`: generic wrappers the baselines lean on for print layout
 * — `.letter-body`, `.title-page`, `.letter-signature` hold blocks, while
 * `.letter-meta > div` and `.address-block` hold bare inline text.
 *
 * ProseMirror can't express "either", and a single `content: 'block+'` node
 * silently wraps inline text in a `<p>` on load, which adds a paragraph's
 * margins to every line of the address block and letter header. So the two
 * cases are separate node types, discriminated at parse time by whether the
 * element actually has a block child. Both serialize back to a plain `<div>`.
 *
 * Default parse priority, so `div[data-block]` and `div[data-computed-page]`
 * (both higher) still win for their own elements.
 */
export const Div = Node.create({
  name: 'div',
  group: 'block',
  content: 'block+',
  defining: true,

  addAttributes() {
    return { ...classAttribute };
  },

  parseHTML() {
    return [
      {
        tag: 'div',
        getAttrs: (element) =>
          hasBlockChild(element as HTMLElement) ? null : false,
      },
    ];
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes), 0];
  },
});

export const InlineDiv = Node.create({
  name: 'inlineDiv',
  group: 'block',
  content: 'inline*',
  defining: true,

  addAttributes() {
    return { ...classAttribute };
  },

  parseHTML() {
    return [
      {
        tag: 'div',
        getAttrs: (element) =>
          hasBlockChild(element as HTMLElement) ? false : null,
      },
    ];
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes), 0];
  },
});

/**
 * `ClassSpan`: inline `<span class="…">`, as a mark rather than a node so it
 * composes with text and other marks. Covers `.bold`, `.toc-entry`, and
 * `.toc-page` — 49 instances across the baselines. `Variable` outranks it, so
 * `span[data-var]` stays a chip.
 */
export const ClassSpan = Mark.create({
  name: 'classSpan',

  addAttributes() {
    return {
      class: {
        default: null,
        parseHTML: (element) => (element as HTMLElement).getAttribute('class'),
        renderHTML: (attributes) =>
          attributes.class ? { class: attributes.class } : {},
      },
    };
  },

  parseHTML() {
    return [
      {
        tag: 'span[class]',
        // Never claim a chip; Variable owns those.
        getAttrs: (element) =>
          (element as HTMLElement).hasAttribute('data-var') ? false : null,
      },
    ];
  },

  renderHTML({ HTMLAttributes }) {
    return ['span', mergeAttributes(HTMLAttributes), 0];
  },
});

/**
 * `DocSection`: one narrative document inside the single continuous editor.
 * Carries `data-doc-id` so save can walk the top-level sections and write only
 * the ones whose HTML actually changed.
 */
export const DocSection = Node.create({
  name: 'docSection',
  group: 'block',
  content: 'block+',
  defining: true,
  isolating: true,

  addAttributes() {
    return {
      docId: {
        default: null,
        parseHTML: (element) => (element as HTMLElement).getAttribute('data-doc-id'),
        renderHTML: (attributes) => ({ 'data-doc-id': attributes.docId }),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'section[data-doc-id]', priority: 100 }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['section', mergeAttributes(HTMLAttributes), 0];
  },
});

/**
 * `ComputedPage`: a read-only card standing in for a financial schedule or the
 * §5570 form, so the continuous editor reads in true package order. It exists
 * only in the editor — save walks `DocSection` nodes and ignores these.
 */
export const ComputedPage = Node.create({
  name: 'computedPage',
  group: 'block',
  atom: true,
  selectable: false,
  draggable: false,
  priority: 200,

  addAttributes() {
    return {
      label: { default: '' },
      pageCount: { default: null },
    };
  },

  parseHTML() {
    return [{ tag: 'div[data-computed-page]', priority: 100 }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-computed-page': '' })];
  },
});

export interface IndentOptions {
  types: string[];
  maxLevel: number;
}

/**
 * `Indent`: an `indent` (0-4) attribute on paragraphs/list items, rendered as
 * `class="indent-N"`. `mergeAttributes` concatenates `class`, so this coexists
 * with the freeform class attribute below — provided that one strips
 * `indent-N` on parse (see `classWithoutIndent`), or the token would be
 * duplicated on every round trip.
 */
export const Indent = Extension.create<IndentOptions>({
  name: 'indent',

  addOptions() {
    return {
      types: ['paragraph', 'listItem'],
      maxLevel: 4,
    };
  },

  addGlobalAttributes() {
    return [
      {
        types: this.options.types,
        attributes: {
          indent: {
            default: 0,
            parseHTML: (element) => {
              const match = /indent-(\d)/.exec(
                (element as HTMLElement).getAttribute('class') || '',
              );
              return match ? Number(match[1]) : 0;
            },
            renderHTML: (attributes) => {
              const level = (attributes as { indent?: number }).indent;
              if (!level) return {};
              return { class: `indent-${level}` };
            },
          },
        },
      },
    ];
  },

  addCommands() {
    const step =
      (delta: number) =>
      () =>
      ({ tr, state, dispatch }: { tr: any; state: any; dispatch: any }) => {
        const { from, to } = state.selection;
        const types = this.options.types;
        const maxLevel = this.options.maxLevel;
        let changed = false;
        // nodesBetween on a collapsed cursor still visits the enclosing block,
        // so this covers "cursor in a paragraph" and a multi-block selection.
        state.doc.nodesBetween(from, to, (node: any, pos: number) => {
          if (types.includes(node.type.name)) {
            const current = node.attrs.indent || 0;
            const next = Math.max(0, Math.min(maxLevel, current + delta));
            if (next !== current) {
              tr.setNodeAttribute(pos, 'indent', next);
              changed = true;
            }
          }
        });
        if (changed && dispatch) dispatch(tr);
        return changed;
      };
    return {
      indent: step(1),
      outdent: step(-1),
    };
  },
});

/**
 * `PreserveClass`: freeform `class` on the block types the baselines style —
 * `.legal-block`, `.muted`, `.disclosure-list`, `.totals-row`, `.title-*`, and
 * the rest. Without it, ProseMirror keeps the element and drops the class, so
 * the PDF loses its styling with no visible error anywhere.
 */
export const PreserveClass = Extension.create({
  name: 'preserveClass',

  addGlobalAttributes() {
    return [
      {
        types: CLASS_BEARING_TYPES,
        attributes: {
          class: {
            default: null,
            parseHTML: (element) => classWithoutIndent(element as HTMLElement),
            renderHTML: (attributes) =>
              attributes.class ? { class: attributes.class } : {},
          },
        },
      },
    ];
  },
});

/**
 * Lists that admit a `ComputedBlock` alongside ordinary list items.
 *
 * Without this the default `listItem+` content expression makes a
 * `<li data-block>` chip illegal inside its own list, so ProseMirror lifts it
 * out on load — which is how §5300 escapes the cover letter's disclosure list,
 * Note 7's assumptions escape their `<ul>`, and the TOC's appendix rows escape
 * theirs.
 */
export const BulletListWithChips = BulletList.extend({
  content: '(listItem|computedBlock)+',
});

export const OrderedListWithChips = OrderedList.extend({
  content: '(listItem|computedBlock)+',
});

/**
 * `PlainTable`: a table that serializes to plain `<table><tbody>` markup.
 *
 * TipTap's default emits a `<colgroup>` and `style="min-width: …"` for the
 * column-resize feature. The backend strips both (neither is in the content
 * model), so every table would come back from a save differing from what the
 * editor produced — which makes every table-bearing document permanently
 * "dirty" and re-saves it on every visit. Emitting clean markup keeps the
 * editor's output and the stored HTML identical.
 */
export const PlainTable = Table.extend({
  renderHTML({ HTMLAttributes }) {
    return ['table', mergeAttributes(HTMLAttributes), ['tbody', 0]];
  },
});

/**
 * Full-document mode's top level: only whole documents and computed-page cards.
 * Stops an operator from deleting a section boundary and silently dropping a
 * document out of the save set.
 */
export const SectionedDocument = Document.extend({
  content: '(docSection|computedPage)+',
});

/**
 * React NodeView renderers, keyed by node name. Supplied by
 * `boilerplateEditorExtensions.tsx`; omitted by the round-trip test.
 *
 * Typed opaquely so this file stays JSX-free (and therefore importable by a
 * plain Node test). A NodeView affects only the live editing DOM — never
 * parse/serialize — so the schema is identical either way, which is what
 * makes the test's verdict apply to what actually ships.
 */
export type NodeViewMap = Partial<Record<'variable' | 'computedBlock' | 'computedPage', unknown>>;

export interface SchemaOptions {
  variableLabels: Record<string, string>;
  blockLabels: Record<string, string>;
  /** Full-document mode: sections, computed-page cards, and a locked top level. */
  withSections: boolean;
  nodeViews?: NodeViewMap;
}

function withView<T extends { extend: (config: any) => T }>(
  node: T,
  view: unknown | undefined,
): T {
  return view ? node.extend({ addNodeView: () => view }) : node;
}

/**
 * The extension list, schema-identical to what the app runs.
 *
 * `withSections` also locks the document's top level to
 * `(docSection | computedPage)+`, so an operator cannot delete a section
 * boundary and silently drop a document from the save set.
 */
export function buildSchemaExtensions({
  variableLabels,
  blockLabels,
  withSections,
  nodeViews,
}: SchemaOptions): Extensions {
  return [
    StarterKit.configure({
      // Headings are ON (levels 1-3): every Note and title page opens with one,
      // and the sanitizer allows h1-h3. The rest stay off because the
      // disclosure print CSS and the backend allowlist don't support them.
      heading: { levels: [1, 2, 3] },
      blockquote: false,
      code: false,
      codeBlock: false,
      horizontalRule: false,
      strike: false,
      link: false,
      // Replaced below with content expressions that admit ComputedBlock, so a
      // `<li data-block>` chip can stay inside its list.
      bulletList: false,
      orderedList: false,
      // Full-document mode swaps in a document whose top level is locked.
      ...(withSections ? { document: false } : {}),
    }),
    ...(withSections ? [SectionedDocument] : []),
    BulletListWithChips,
    OrderedListWithChips,
    // Narrative tables are ordinary editable tables (design.md D3): the operator
    // owns rows, labels, and structure, while the numbers inside the cells stay
    // value chips the system fills in.
    PlainTable.configure({ resizable: false }),
    TableRow,
    TableHeader,
    TableCell,
    Div,
    InlineDiv,
    ClassSpan,
    Indent,
    PreserveClass,
    withView(Variable, nodeViews?.variable).configure({ labels: variableLabels }),
    withView(ComputedBlock, nodeViews?.computedBlock).configure({
      labels: blockLabels,
    }),
    ...(withSections
      ? [DocSection, withView(ComputedPage, nodeViews?.computedPage)]
      : []),
  ];
}
