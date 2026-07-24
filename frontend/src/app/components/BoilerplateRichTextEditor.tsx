/**
 * Rich-text editor for a single boilerplate slot (add-boilerplate-rich-text-editor).
 *
 * Exposes exactly bold, bullet/numbered lists, and indentation — the
 * formatting set the disclosure print CSS and the backend nh3 allowlist
 * (`boilerplate_sanitize.py`) both support. Variable tokens are inserted as
 * chips via the picker and stored as `<span data-var="NAME">` (see
 * `boilerplateEditorExtensions.tsx`), resolved server-side at compile time —
 * never evaluated in the browser.
 */
import { useEffect } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import {
  Bold as BoldIcon,
  IndentDecrease,
  IndentIncrease,
  List as ListIcon,
  ListOrdered,
} from 'lucide-react';
import type { BoilerplateVariable } from '../api/hoaSettings';
import { Indent, Variable } from './boilerplateEditorExtensions';
import { Button } from './ui/button';

export function BoilerplateRichTextEditor({
  value,
  onChange,
  variables,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (html: string) => void;
  variables: BoilerplateVariable[];
  placeholder?: string;
  disabled?: boolean;
}) {
  const labels = Object.fromEntries(variables.map((v) => [v.id, v.label]));

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        // Only bold + lists survive — everything else the disclosure
        // print CSS / sanitizer allowlist doesn't support stays off.
        heading: false,
        blockquote: false,
        code: false,
        codeBlock: false,
        horizontalRule: false,
        italic: false,
        strike: false,
        underline: false,
        link: false,
      }),
      Indent,
      Variable.configure({ labels }),
    ],
    content: value || '<p></p>',
    editable: !disabled,
    editorProps: {
      attributes: {
        class:
          'min-h-[min(50vh,360px)] w-full flex-1 rounded-md border border-[#d4d4d4] px-3 py-2 text-sm text-[#1a1a1a] focus:outline-none focus:ring-2 focus:ring-[#1a1a1a] letter-body',
      },
    },
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
      <div className="mb-2 flex flex-wrap items-center gap-1 rounded-md border border-[#d4d4d4] bg-[#fafafa] p-1">
        <Button
          type="button"
          variant={editor.isActive('bold') ? 'default' : 'ghost'}
          size="sm"
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleBold().run()}
          aria-label="Bold"
          aria-pressed={editor.isActive('bold')}
        >
          <BoldIcon className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          variant={editor.isActive('bulletList') ? 'default' : 'ghost'}
          size="sm"
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleBulletList().run()}
          aria-label="Bullet list"
          aria-pressed={editor.isActive('bulletList')}
        >
          <ListIcon className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          variant={editor.isActive('orderedList') ? 'default' : 'ghost'}
          size="sm"
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleOrderedList().run()}
          aria-label="Numbered list"
          aria-pressed={editor.isActive('orderedList')}
        >
          <ListOrdered className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => editor.chain().focus().outdent().run()}
          aria-label="Decrease indent"
        >
          <IndentDecrease className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => editor.chain().focus().indent().run()}
          aria-label="Increase indent"
        >
          <IndentIncrease className="h-4 w-4" />
        </Button>
        <div className="mx-1 h-5 w-px bg-[#d4d4d4]" />
        <label className="sr-only" htmlFor="bp-insert-variable">
          Insert variable
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
            Insert variable…
          </option>
          {variables.map((v) => (
            <option key={v.id} value={v.id}>
              {v.label}
            </option>
          ))}
        </select>
      </div>
      <EditorContent editor={editor} className="flex min-h-0 flex-1 flex-col" />
      {!value && placeholder && (
        <p className="mt-1 text-xs text-[#888888]">{placeholder}</p>
      )}
    </div>
  );
}
