/**
 * The editing surface for the whole disclosure package.
 *
 * Split out of `BoilerplateWorkbench` so the editor is constructed exactly
 * once, with the chip labels already loaded. Previously `useEditor` listed the
 * label maps in its dependency array, so the editor was rebuilt the moment the
 * API responded — *after* the content had been pushed into the old instance —
 * blanking the document until an accidental refetch put it back.
 *
 * Two invariants this component owns:
 *
 * 1. **Dirty state is measured in the editor's own serialization.** The
 *    document each section was loaded with is snapshotted via the same
 *    `getHTMLFromFragment` used at save time. Comparing against the raw API
 *    HTML instead (which is what shipped) marks every document dirty forever,
 *    so pressing Save with no edits rewrites all 14 — at firm scope, for every
 *    HOA.
 * 2. **Saves are atomic.** Changed sections go out in one bulk request.
 */
import { useCallback, useEffect, useImperativeHandle, useMemo, useRef } from 'react';
import { forwardRef } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import { getHTMLFromFragment } from '@tiptap/core';
import type { Editor } from '@tiptap/react';
import type {
  NarrativeDocument,
  NarrativeEditableDocument,
} from '../api/hoaSettings';
import {
  DOCUMENT_EDITOR_CLASS,
  EditorToolbar,
  buildEditorExtensions,
} from './BoilerplateRichTextEditor';

export function isEditable(
  doc: NarrativeDocument,
): doc is NarrativeEditableDocument {
  return doc.kind === 'editable';
}

function escapeAttr(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

/** Serialize the API's document list into one editor document. */
export function buildDocumentHtml(documents: NarrativeDocument[]): string {
  return documents
    .map((doc) => {
      if (isEditable(doc)) {
        return `<section data-doc-id="${doc.id}">${doc.html || '<p></p>'}</section>`;
      }
      return (
        `<div data-computed-page label="${escapeAttr(doc.label)}" ` +
        `pageCount="${doc.page_count_hint}"></div>`
      );
    })
    .join('');
}

/** Each top-level DocSection's current HTML, keyed by document id. */
export function snapshotSections(editor: Editor): Record<string, string> {
  const out: Record<string, string> = {};
  editor.state.doc.forEach((node) => {
    if (node.type.name !== 'docSection') return;
    const docId = node.attrs.docId as string | null;
    if (docId) out[docId] = getHTMLFromFragment(node.content, editor.schema);
  });
  return out;
}

export interface NarrativeEditorHandle {
  /** Documents whose content differs from what was loaded. */
  changedDocuments: () => Record<string, string>;
  /** Re-baseline after a successful save, so the same edits aren't re-sent. */
  markSaved: () => void;
  activeDocId: () => string | null;
}

export const NarrativeDocumentEditor = forwardRef<
  NarrativeEditorHandle,
  {
    documents: NarrativeDocument[];
    variables: { id: string; label: string }[];
    blocks: { id: string; label: string }[];
    disabled?: boolean;
    onActiveDocChange: (docId: string | null) => void;
    onDirtyChange: (dirty: boolean) => void;
  }
>(function NarrativeDocumentEditor(
  { documents, variables, blocks, disabled, onActiveDocChange, onDirtyChange },
  ref,
) {
  const variableLabels = useMemo(
    () => Object.fromEntries(variables.map((v) => [v.id, v.label])),
    [variables],
  );
  const blockLabels = useMemo(
    () => Object.fromEntries(blocks.map((b) => [b.id, b.label])),
    [blocks],
  );

  // The editor's own serialization of each section as loaded — the only
  // baseline a diff against `getHTMLFromFragment` can meaningfully use.
  const loadedRef = useRef<Record<string, string>>({});
  const activeRef = useRef<string | null>(null);

  const editor = useEditor({
    extensions: buildEditorExtensions({
      variableLabels,
      blockLabels,
      withSections: true,
    }),
    content: buildDocumentHtml(documents),
    editable: !disabled,
    editorProps: { attributes: { class: DOCUMENT_EDITOR_CLASS } },
    onCreate: ({ editor: e }) => {
      loadedRef.current = snapshotSections(e);
    },
    onUpdate: ({ editor: e }) => {
      const current = snapshotSections(e);
      const dirty = Object.keys(current).some(
        (id) => current[id] !== loadedRef.current[id],
      );
      onDirtyChange(dirty);
    },
    onSelectionUpdate: ({ editor: e }) => {
      const { $from } = e.state.selection;
      for (let depth = $from.depth; depth > 0; depth -= 1) {
        const node = $from.node(depth);
        if (node.type.name === 'docSection') {
          const docId = node.attrs.docId as string;
          if (activeRef.current !== docId) {
            activeRef.current = docId;
            onActiveDocChange(docId);
          }
          return;
        }
      }
    },
  });

  // A fresh `documents` prop means the server content changed under us (a save
  // or a reset). Re-baseline together with the content so the two never drift.
  useEffect(() => {
    if (!editor) return;
    editor.commands.setContent(buildDocumentHtml(documents), { emitUpdate: false });
    loadedRef.current = snapshotSections(editor);
    onDirtyChange(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documents, editor]);

  useEffect(() => {
    editor?.setEditable(!disabled);
  }, [disabled, editor]);

  const changedDocuments = useCallback(() => {
    if (!editor) return {};
    const current = snapshotSections(editor);
    const changed: Record<string, string> = {};
    for (const [docId, html] of Object.entries(current)) {
      if (html !== loadedRef.current[docId]) changed[docId] = html;
    }
    return changed;
  }, [editor]);

  useImperativeHandle(
    ref,
    () => ({
      changedDocuments,
      markSaved: () => {
        if (editor) loadedRef.current = snapshotSections(editor);
        onDirtyChange(false);
      },
      activeDocId: () => activeRef.current,
    }),
    [changedDocuments, editor, onDirtyChange],
  );

  if (!editor) return null;

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-b border-[#e5e5e5] bg-white px-4 py-1.5">
        <EditorToolbar
          editor={editor}
          variables={variables}
          blocks={blocks}
          disabled={disabled}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-[#f5f5f5]">
        <div className="mx-auto my-6 max-w-[8.5in] rounded bg-white shadow-sm">
          <EditorContent editor={editor} />
        </div>
      </div>
    </>
  );
});
