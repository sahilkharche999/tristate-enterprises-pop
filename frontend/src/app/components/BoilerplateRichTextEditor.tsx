/**
 * Rich-text editor for narrative disclosure content.
 *
 * Two modes share one editor so the formatting vocabulary can never diverge:
 *
 * - single-slot (legacy boilerplate workbench): one body, no sections;
 * - full-document (add-full-document-editor): one continuous document holding
 *   every narrative page as a `DocSection`, edited like a Word document.
 *
 * The exposed formatting set — headings, bold, lists, indentation, tables — is
 * exactly the content model the backend nh3 allowlist accepts
 * (`boilerplate_sanitize.CONTENT_MODEL_TAGS`). Anything this editor can
 * produce must survive the sanitizer, or it is silently deleted on save.
 *
 * Computed values are chips, never typed text: `<span data-var="NAME">` for
 * scalars and `<div|li data-block="NAME">` for system-generated blocks. Both
 * are resolved server-side at compile time — never evaluated in the browser.
 */
import { useEffect, useMemo, useState } from 'react';
import type { Editor } from '@tiptap/react';
import { EditorContent, useEditor } from '@tiptap/react';
import {
  Bold as BoldIcon,
  Columns3,
  Heading1,
  Heading2,
  Heading3,
  IndentDecrease,
  IndentIncrease,
  List as ListIcon,
  ListOrdered,
  Rows3,
  Table as TableIcon,
  Trash2,
} from 'lucide-react';
import type { BoilerplateVariable } from '../api/hoaSettings';
import { buildEditorExtensions } from './boilerplateEditorExtensions';
import { Button } from './ui/button';

export { buildEditorExtensions };

const EDITOR_CLASS =
  'min-h-[min(50vh,360px)] w-full flex-1 rounded-md border border-[#d4d4d4] px-3 py-2 text-sm text-[#1a1a1a] focus:outline-none focus:ring-2 focus:ring-[#1a1a1a] letter-body';

export const FONT_FACE_CLASSES = [
  { id: 'font-condensed', label: 'Condensed (current)' },
  { id: 'font-sans', label: 'Sans' },
  { id: 'font-serif', label: 'Serif' },
  { id: 'font-dejavu-sans', label: 'DejaVu Sans' },
  { id: 'font-dejavu-serif', label: 'DejaVu Serif' },
] as const;

export const FONT_SIZE_CLASSES = [
  { id: 'text-8pt', label: '8 pt' },
  { id: 'text-9pt', label: '9 pt' },
  { id: 'text-10pt', label: '10 pt' },
  { id: 'text-11pt', label: '11 pt' },
  { id: 'text-12pt', label: '12 pt' },
  { id: 'text-14pt', label: '14 pt' },
] as const;

const FONT_FACE_IDS = new Set<string>(FONT_FACE_CLASSES.map((item) => item.id));
const FONT_SIZE_IDS = new Set<string>(FONT_SIZE_CLASSES.map((item) => item.id));

/** Swap one face or size class and keep every other class on the span. */
export function replaceTypographyClass(
  current: string,
  group: Set<string>,
  next: string,
): string {
  const kept = current.split(/\s+/).filter((token) => token && !group.has(token));
  if (!kept.includes(next)) kept.push(next);
  return kept.join(' ');
}

function activeTypographyClass(className: string, group: Set<string>): string {
  return className.split(/\s+/).find((token) => group.has(token)) ?? '';
}

const DOCUMENT_EDITOR_CLASS =
  'min-h-full w-full flex-1 px-6 py-4 text-sm text-[#1a1a1a] focus:outline-none letter-body';

function ToolbarButton({
  active,
  disabled,
  onClick,
  label,
  children,
}: {
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <Button
      type="button"
      variant={active ? 'default' : 'ghost'}
      size="sm"
      disabled={disabled}
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-pressed={active}
    >
      {children}
    </Button>
  );
}

export function EditorToolbar({
  editor,
  variables,
  blocks,
  disabled,
}: {
  editor: Editor;
  variables: BoilerplateVariable[];
  blocks?: BoilerplateVariable[];
  disabled?: boolean;
}) {
  const inTable = editor.isActive('table');
  const [, setSelectionTick] = useState(0);
  useEffect(() => {
    const bump = () => setSelectionTick((n) => n + 1);
    editor.on('selectionUpdate', bump);
    editor.on('transaction', bump);
    return () => {
      editor.off('selectionUpdate', bump);
      editor.off('transaction', bump);
    };
  }, [editor]);
  const activeClass = String(editor.getAttributes('classSpan').class || '');
  const activeFace = activeTypographyClass(activeClass, FONT_FACE_IDS);
  const activeSize = activeTypographyClass(activeClass, FONT_SIZE_IDS);
  const applyTypography = (group: Set<string>, next: string) => {
    const className = replaceTypographyClass(activeClass, group, next);
    editor.chain().focus().setMark('classSpan', { class: className }).run();
  };

  return (
    <div className="flex flex-wrap items-center gap-1 rounded-md border border-[#d4d4d4] bg-[#fafafa] p-1">
      {([1, 2, 3] as const).map((level) => {
        const Icon = level === 1 ? Heading1 : level === 2 ? Heading2 : Heading3;
        return (
          <ToolbarButton
            key={level}
            active={editor.isActive('heading', { level })}
            disabled={disabled}
            onClick={() => editor.chain().focus().toggleHeading({ level }).run()}
            label={`Heading ${level}`}
          >
            <Icon className="h-4 w-4" />
          </ToolbarButton>
        );
      })}
      <div className="mx-1 h-5 w-px bg-[#d4d4d4]" />
      <ToolbarButton
        active={editor.isActive('bold')}
        disabled={disabled}
        onClick={() => editor.chain().focus().toggleBold().run()}
        label="Bold"
      >
        <BoldIcon className="h-4 w-4" />
      </ToolbarButton>
      <label className="sr-only" htmlFor="bp-font-face">
        Font
      </label>
      <select
        id="bp-font-face"
        className="rounded border border-[#d4d4d4] bg-white px-2 py-1 text-xs text-[#1a1a1a] disabled:opacity-50"
        disabled={disabled}
        value={activeFace}
        onChange={(e) => {
          if (e.target.value) applyTypography(FONT_FACE_IDS, e.target.value);
        }}
      >
        <option value="">Font</option>
        {FONT_FACE_CLASSES.map((item) => (
          <option key={item.id} value={item.id}>
            {item.label}
          </option>
        ))}
      </select>
      <label className="sr-only" htmlFor="bp-font-size">
        Font size
      </label>
      <select
        id="bp-font-size"
        className="rounded border border-[#d4d4d4] bg-white px-2 py-1 text-xs text-[#1a1a1a] disabled:opacity-50"
        disabled={disabled}
        value={activeSize}
        onChange={(e) => {
          if (e.target.value) applyTypography(FONT_SIZE_IDS, e.target.value);
        }}
      >
        <option value="">Size</option>
        {FONT_SIZE_CLASSES.map((item) => (
          <option key={item.id} value={item.id}>
            {item.label}
          </option>
        ))}
      </select>
      <ToolbarButton
        active={editor.isActive('bulletList')}
        disabled={disabled}
        onClick={() => editor.chain().focus().toggleBulletList().run()}
        label="Bullet list"
      >
        <ListIcon className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        active={editor.isActive('orderedList')}
        disabled={disabled}
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
        label="Numbered list"
      >
        <ListOrdered className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        disabled={disabled}
        onClick={() => editor.chain().focus().outdent().run()}
        label="Decrease indent"
      >
        <IndentDecrease className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        disabled={disabled}
        onClick={() => editor.chain().focus().indent().run()}
        label="Increase indent"
      >
        <IndentIncrease className="h-4 w-4" />
      </ToolbarButton>

      <div className="mx-1 h-5 w-px bg-[#d4d4d4]" />
      <ToolbarButton
        disabled={disabled}
        onClick={() =>
          editor
            .chain()
            .focus()
            .insertTable({ rows: 3, cols: 2, withHeaderRow: true })
            .run()
        }
        label="Insert table"
      >
        <TableIcon className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        disabled={disabled || !inTable}
        onClick={() => editor.chain().focus().addRowAfter().run()}
        label="Add row"
      >
        <Rows3 className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        disabled={disabled || !inTable}
        onClick={() => editor.chain().focus().addColumnAfter().run()}
        label="Add column"
      >
        <Columns3 className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        disabled={disabled || !inTable}
        onClick={() => editor.chain().focus().deleteRow().run()}
        label="Delete row"
      >
        <Trash2 className="h-4 w-4" />
      </ToolbarButton>

      <div className="mx-1 h-5 w-px bg-[#d4d4d4]" />
      <label className="sr-only" htmlFor="bp-insert-variable">
        Insert value
      </label>
      <select
        id="bp-insert-variable"
        className="rounded border border-[#d4d4d4] bg-white px-2 py-1 text-xs text-[#1a1a1a] disabled:opacity-50"
        disabled={disabled || variables.length === 0}
        value=""
        onChange={(e) => {
          const name = e.target.value;
          if (name) editor.chain().focus().insertVariable(name).run();
          e.target.value = '';
        }}
      >
        <option value="" disabled>
          Insert value…
        </option>
        {variables.map((v) => (
          <option key={v.id} value={v.id}>
            {v.label}
          </option>
        ))}
      </select>

      {/* Block chips are deletable by design, so there has to be a way back in
          that isn't "reset the whole document and lose your edits". */}
      {blocks && blocks.length > 0 && (
        <>
          <label className="sr-only" htmlFor="bp-insert-block">
            Insert block
          </label>
          <select
            id="bp-insert-block"
            className="rounded border border-[#d4d4d4] bg-white px-2 py-1 text-xs text-[#1a1a1a] disabled:opacity-50"
            disabled={disabled}
            value=""
            onChange={(e) => {
              const name = e.target.value;
              if (name) editor.chain().focus().insertComputedBlock(name).run();
              e.target.value = '';
            }}
          >
            <option value="" disabled>
              Insert block…
            </option>
            {blocks.map((b) => (
              <option key={b.id} value={b.id}>
                {b.label}
              </option>
            ))}
          </select>
        </>
      )}
    </div>
  );
}

export function BoilerplateRichTextEditor({
  value,
  onChange,
  variables,
  blocks,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (html: string) => void;
  variables: BoilerplateVariable[];
  blocks?: BoilerplateVariable[];
  placeholder?: string;
  disabled?: boolean;
}) {
  const variableLabels = useMemo(
    () => Object.fromEntries(variables.map((v) => [v.id, v.label])),
    [variables],
  );
  const blockLabels = useMemo(
    () => Object.fromEntries((blocks || []).map((b) => [b.id, b.label])),
    [blocks],
  );

  const editor = useEditor({
    extensions: buildEditorExtensions({
      variableLabels,
      blockLabels,
      withSections: false,
    }),
    content: value || '<p></p>',
    editable: !disabled,
    editorProps: { attributes: { class: EDITOR_CLASS } },
    onUpdate: ({ editor: e }) => onChange(e.getHTML()),
  });

  // Keep the editor in sync when switching slots (a fresh `value` prop for
  // the same mounted editor instance) without clobbering active typing.
  useEffect(() => {
    if (!editor) return;
    const current = editor.getHTML();
    const next = value || '<p></p>';
    if (current !== next) {
      editor.commands.setContent(next, { emitUpdate: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, editor]);

  useEffect(() => {
    editor?.setEditable(!disabled);
  }, [disabled, editor]);

  if (!editor) return null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-2">
        <EditorToolbar
          editor={editor}
          variables={variables}
          blocks={blocks}
          disabled={disabled}
        />
      </div>
      <EditorContent editor={editor} className="flex min-h-0 flex-1 flex-col" />
      {!value && placeholder && (
        <p className="mt-1 text-xs text-[#888888]">{placeholder}</p>
      )}
    </div>
  );
}

export { DOCUMENT_EDITOR_CLASS };
