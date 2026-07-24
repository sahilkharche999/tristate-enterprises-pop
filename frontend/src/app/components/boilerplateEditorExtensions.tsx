/**
 * TipTap extensions for the boilerplate rich-text editor
 * (add-boilerplate-rich-text-editor).
 *
 * `Variable`: an atomic inline node serializing to `<span data-var="NAME">`
 * — the exact token format `boilerplate_variables.resolve` (backend) parses.
 * The NodeView below only affects the live editing DOM; `editor.getHTML()`
 * serializes via `renderHTML`/schema `toDOM`, independent of the NodeView,
 * so the stored value is always the plain span the backend expects.
 *
 * `Indent`: adds an `indent` (0-4) attribute to paragraphs/list items,
 * rendered as `class="indent-N"` — matching the `class`-on-any-tag
 * allowlist in `boilerplate_sanitize.py` and the `.indent-N` rules in
 * `_shared.css`.
 */
import { Extension, Node, mergeAttributes } from '@tiptap/core';
import { NodeViewWrapper, ReactNodeViewRenderer } from '@tiptap/react';

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    variableChip: {
      insertVariable: (name: string) => ReturnType;
    };
    indent: {
      indent: () => ReturnType;
      outdent: () => ReturnType;
    };
  }
}

export interface VariableOptions {
  labels: Record<string, string>;
}

function VariableChipView({ node, extension }: { node: { attrs: { name: string } }; extension: { options: VariableOptions } }) {
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

export const Variable = Node.create<VariableOptions>({
  name: 'variable',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,

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
    return [{ tag: 'span[data-var]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['span', mergeAttributes(HTMLAttributes)];
  },

  addNodeView() {
    return ReactNodeViewRenderer(VariableChipView);
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

export interface IndentOptions {
  types: string[];
  maxLevel: number;
}

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
              const match = /indent-(\d)/.exec((element as HTMLElement).getAttribute('class') || '');
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
    const step = (delta: number) =>
      () =>
      ({ tr, state, dispatch }: { tr: any; state: any; dispatch: any }) => {
        const { selection } = state;
        let updated = false;
        state.doc.nodesBetween(selection.from, selection.to, (node: any, pos: number) => {
          if (this.options.types.includes(node.type.name)) {
            const level = Math.max(0, Math.min(this.options.maxLevel, (node.attrs.indent || 0) + delta));
            if (dispatch) tr.setNodeAttribute(pos, 'indent', level);
            updated = true;
          }
        });
        return updated;
      };
    return {
      indent: step(1),
      outdent: step(-1),
    };
  },
});
