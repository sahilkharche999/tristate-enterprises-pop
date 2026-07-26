/**
 * Full-screen document editor for the disclosure package
 * (add-full-document-editor).
 *
 * One continuous scroll of the whole report, already written, edited like a
 * Word document. Every narrative page is a `DocSection`; the financial
 * schedules and the §5570 form appear as read-only cards in their true package
 * positions so the report reads in order without pretending they are editable.
 *
 * Computed figures inside the prose (and inside tables) are chips the system
 * fills in at generation time, so restructuring a table or rewriting a
 * paragraph never disturbs the math.
 *
 * This component owns loading, scope, and the destructive-action guards; the
 * editing surface itself is `NarrativeDocumentEditor`, mounted only once the
 * payload has arrived so the editor is never rebuilt out from under its
 * content.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { FileText, X } from 'lucide-react';
import {
  type BoilerplateReferenceJob,
  type BoilerplateVariable,
  type NarrativeChipValues,
  type NarrativeDocument,
  type NarrativeDocumentsResponse,
  type NarrativeScope,
  boilerplateReferencePdfUrl,
  deleteBoilerplateReferencePdf,
  getNarrativeChipValues,
  getNarrativeDocuments,
  listBoilerplateReferenceJobs,
  putNarrativeDocuments,
  resetNarrativeDocument,
  uploadBoilerplateReferencePdf,
} from '../api/hoaSettings';
import { authHeaders } from '../api/http';
import { getErrorMessage } from '../lib/errors';
import { type ChipInspectorTarget, ChipInspector } from './ChipInspector';
import {
  type NarrativeEditorHandle,
  NarrativeDocumentEditor,
  isEditable,
} from './NarrativeDocumentEditor';
import { Button } from './ui/button';
import { toast } from 'sonner';

type ReferenceSource = 'job' | 'upload';

const SCOPE_HINT: Record<NarrativeScope, string> = {
  firm: 'Saving applies to every HOA that has no override of its own.',
  hoa: 'Saving applies to this HOA only.',
};

export function BoilerplateWorkbench({
  hoaId,
  open,
  onClose,
  hoaName,
  onEditSetting,
}: {
  hoaId: number;
  open: boolean;
  onClose: () => void;
  hoaName?: string;
  /** Reveal the settings field behind a chip. Called after the editor closes. */
  onEditSetting?: (tab: 'disclosure' | 'database', field: string) => void;
}) {
  const [documents, setDocuments] = useState<NarrativeDocument[] | null>(null);
  const [variables, setVariables] = useState<BoilerplateVariable[]>([]);
  const [blocks, setBlocks] = useState<BoilerplateVariable[]>([]);
  const [scope, setScope] = useState<NarrativeScope>('hoa');
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [activeDocId, setActiveDocId] = useState<string | null>(null);
  const [chipValues, setChipValues] = useState<NarrativeChipValues | null>(null);
  const [inspecting, setInspecting] = useState<ChipInspectorTarget | null>(null);
  const [showReference, setShowReference] = useState(false);
  const editorRef = useRef<NarrativeEditorHandle>(null);

  const [jobs, setJobs] = useState<BoilerplateReferenceJob[]>([]);
  const [referenceSource, setReferenceSource] = useState<ReferenceSource>('job');
  const [selectedJobId, setSelectedJobId] = useState<string>('');
  const [hasUpload, setHasUpload] = useState(false);
  const [pdfObjectUrl, setPdfObjectUrl] = useState<string | null>(null);
  const [pdfStatus, setPdfStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>(
    'idle',
  );
  const [uploading, setUploading] = useState(false);

  const applyPayload = useCallback((payload: NarrativeDocumentsResponse) => {
    setDocuments(payload.documents);
    setVariables(payload.variables ?? []);
    setBlocks(payload.blocks ?? []);
  }, []);

  const load = useCallback(async () => {
    try {
      applyPayload(await getNarrativeDocuments(hoaId));
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to load the disclosure documents.'));
    }
  }, [hoaId, applyPayload]);

  useEffect(() => {
    if (!open) return;
    void load();
  }, [open, load]);

  // Chip values are fetched separately from the documents so the editor opens
  // at once — resolving them can mean running the whole compute. Failure is
  // silent by design: every popover still shows the chip's source and its
  // "Edit in settings" link, just without a preview of the value.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setChipValues(null);
    void getNarrativeChipValues(hoaId)
      .then((v) => {
        if (!cancelled) setChipValues(v);
      })
      .catch(() => {
        // An empty map, not null: the popovers must stop saying "Checking…"
        // and fall back to explaining each chip's source instead.
        if (!cancelled) {
          setChipValues({
            fiscal_year: 0,
            computed_available: false,
            unavailable_reason: null,
            values: {},
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [hoaId, open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void listBoilerplateReferenceJobs(hoaId)
      .then((list) => {
        if (cancelled) return;
        setJobs(list);
        setSelectedJobId((prev) => prev || (list[0]?.job_id ?? ''));
      })
      .catch(() => {
        if (!cancelled) setJobs([]);
      });
    return () => {
      cancelled = true;
    };
  }, [hoaId, open]);

  useEffect(() => {
    if (!open || !showReference) {
      setPdfStatus('idle');
      setPdfObjectUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
      return;
    }
    const canLoadJob = referenceSource === 'job' && Boolean(selectedJobId);
    const canLoadUpload = referenceSource === 'upload' && hasUpload;
    if (!canLoadJob && !canLoadUpload) {
      setPdfStatus('idle');
      return;
    }

    let url: string | null = null;
    let cancelled = false;
    setPdfStatus('loading');
    void fetch(
      boilerplateReferencePdfUrl(hoaId, referenceSource, selectedJobId || undefined),
      { headers: authHeaders() },
    )
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('Failed to load PDF'))))
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setPdfObjectUrl(url);
        setPdfStatus('ready');
      })
      .catch(() => {
        if (!cancelled) setPdfStatus('error');
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [hoaId, open, showReference, referenceSource, selectedJobId, hasUpload]);

  const requestClose = useCallback(() => {
    if (
      dirty &&
      !window.confirm('You have unsaved changes. Close without saving?')
    ) {
      return;
    }
    onClose();
  }, [dirty, onClose]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      // The chip popover takes Esc first (it listens in the capture phase);
      // if one is open, Esc dismisses it rather than the whole editor.
      if (e.key === 'Escape' && !inspecting) requestClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, requestClose, inspecting]);

  // The popover is anchored to a rect captured at click time, so it would
  // detach from its chip on scroll. Closing is the honest response.
  useEffect(() => {
    if (!inspecting) return;
    const dismiss = () => setInspecting(null);
    window.addEventListener('scroll', dismiss, true);
    window.addEventListener('resize', dismiss);
    return () => {
      window.removeEventListener('scroll', dismiss, true);
      window.removeEventListener('resize', dismiss);
    };
  }, [inspecting]);

  /**
   * Leave the editor for the settings field that drives a chip.
   *
   * Routed through the same unsaved-changes guard as closing, because that is
   * exactly what this does — the editor is a full-screen overlay over the
   * settings page, and there is no way to show a field without leaving it.
   */
  const handleEditSetting = useCallback(
    (tab: 'disclosure' | 'database', field: string) => {
      if (
        dirty &&
        !window.confirm(
          'You have unsaved changes. Leave the editor to change this setting?',
        )
      ) {
        return;
      }
      setInspecting(null);
      onClose();
      onEditSetting?.(tab, field);
    },
    [dirty, onClose, onEditSetting],
  );

  const handleSave = async () => {
    const changed = editorRef.current?.changedDocuments() ?? {};
    const count = Object.keys(changed).length;
    if (count === 0) {
      toast.info('No changes to save.');
      return;
    }
    if (
      scope === 'firm' &&
      !window.confirm(
        `Save ${count} document(s) as the firm default?\n\n` +
          'This changes the wording for every HOA that does not have its own ' +
          'override. Associations with their own version are unaffected.',
      )
    ) {
      return;
    }

    setSaving(true);
    try {
      // One transaction: a failure partway through must not leave the firm
      // defaults half-rewritten.
      applyPayload(await putNarrativeDocuments(hoaId, changed, scope));
      editorRef.current?.markSaved();
      toast.success(
        scope === 'firm'
          ? `Saved ${count} document(s) as the firm default.`
          : `Saved ${count} document(s) for this HOA.`,
      );
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to save. Nothing was changed.'));
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async (docId: string, label: string) => {
    if (
      !window.confirm(
        scope === 'firm'
          ? `Reset “${label}” to the original shipped wording for every HOA?`
          : `Reset “${label}” for this HOA?`,
      )
    ) {
      return;
    }
    setSaving(true);
    try {
      applyPayload(await resetNarrativeDocument(hoaId, docId, scope));
      toast.success('Reset to the wording underneath.');
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to reset.'));
    } finally {
      setSaving(false);
    }
  };

  const handleUpload = async (file: File | null) => {
    if (!file) return;
    setUploading(true);
    try {
      await uploadBoilerplateReferencePdf(hoaId, file);
      setHasUpload(true);
      setReferenceSource('upload');
      toast.success('Reference PDF uploaded.');
    } catch (error) {
      toast.error(getErrorMessage(error, 'Upload failed.'));
    } finally {
      setUploading(false);
    }
  };

  const handleClearUpload = async () => {
    try {
      await deleteBoilerplateReferencePdf(hoaId);
      setHasUpload(false);
      toast.success('Uploaded reference cleared.');
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to clear reference.'));
    }
  };

  if (!open) return null;

  const activeDoc = (documents ?? [])
    .filter(isEditable)
    .find((d) => d.id === activeDocId);
  const canResetActive =
    activeDoc &&
    (scope === 'firm' ? activeDoc.has_firm_override : activeDoc.has_hoa_override);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-white"
      role="dialog"
      aria-modal="true"
      aria-label="Disclosure package document editor"
    >
      <div className="flex items-center justify-between gap-4 border-b border-[#e5e5e5] bg-white px-4 py-2.5">
        <div className="min-w-0">
          <p className="text-sm font-medium text-[#111111]">
            Disclosure package{hoaName ? ` · ${hoaName}` : ''}
            {dirty && <span className="ml-2 text-xs text-[#b45309]">Unsaved</span>}
          </p>
          <p className="truncate text-xs text-[#737373]">
            {activeDoc ? `Editing: ${activeDoc.label}` : 'Click anywhere to edit'} ·{' '}
            {SCOPE_HINT[scope]}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {activeDoc && (
            <>
              <span
                className={`rounded px-1.5 py-0.5 text-xs ${
                  activeDoc.effective_scope === 'hoa'
                    ? 'bg-emerald-50 text-emerald-800'
                    : activeDoc.effective_scope === 'firm'
                      ? 'bg-blue-50 text-blue-800'
                      : 'bg-[#f5f5f5] text-[#737373]'
                }`}
              >
                {activeDoc.effective_scope === 'hoa'
                  ? 'Custom for this HOA'
                  : activeDoc.effective_scope === 'firm'
                    ? 'Firm default'
                    : 'Original wording'}
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={saving || !canResetActive}
                onClick={() => void handleReset(activeDoc.id, activeDoc.label)}
              >
                Reset this page
              </Button>
            </>
          )}
          <label className="sr-only" htmlFor="bp-scope">
            Save scope
          </label>
          <select
            id="bp-scope"
            className="rounded border border-[#d4d4d4] bg-white px-2 py-1 text-xs text-[#1a1a1a]"
            value={scope}
            onChange={(e) => setScope(e.target.value as NarrativeScope)}
            disabled={saving}
          >
            <option value="firm">Firm default — applies to all HOAs</option>
            <option value="hoa">This HOA only</option>
          </select>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setShowReference((v) => !v)}
            aria-pressed={showReference}
            title="Show a previously generated package alongside"
          >
            <FileText className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || !documents}
          >
            {saving ? 'Saving…' : 'Save'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={requestClose}
            aria-label="Close document editor"
          >
            <X className="h-5 w-5 text-[#525252]" />
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex min-h-0 flex-1 flex-col">
          {documents === null ? (
            <p className="p-6 text-sm text-[#666666]">
              Loading the disclosure package…
            </p>
          ) : (
            // Mounted only once the payload exists, so the editor is built with
            // its chip labels already in place and never rebuilt underneath its
            // own content.
            <NarrativeDocumentEditor
              ref={editorRef}
              documents={documents}
              variables={variables}
              blocks={blocks}
              disabled={saving}
              onActiveDocChange={setActiveDocId}
              onDirtyChange={setDirty}
              onInspectChip={setInspecting}
            />
          )}
        </div>

        {showReference && (
          <div className="flex min-h-0 w-[45%] shrink-0 flex-col border-l border-[#e5e5e5] bg-[#fafafa]">
            <div className="flex flex-wrap items-center gap-2 border-b border-[#e5e5e5] bg-white px-3 py-2">
              <span className="text-sm font-medium text-[#1a1a1a]">Reference PDF</span>
              <select
                className="rounded border border-[#d4d4d4] px-2 py-1 text-sm"
                value={referenceSource}
                onChange={(e) => setReferenceSource(e.target.value as ReferenceSource)}
              >
                <option value="job">This app&apos;s package</option>
                <option value="upload">Upload PDF</option>
              </select>
              {referenceSource === 'job' ? (
                jobs.length === 0 ? (
                  <span className="text-xs text-[#666666]">
                    No completed packages — try Upload
                  </span>
                ) : (
                  <select
                    className="max-w-[min(100%,240px)] rounded border border-[#d4d4d4] px-2 py-1 text-sm"
                    value={selectedJobId}
                    onChange={(e) => setSelectedJobId(e.target.value)}
                  >
                    {jobs.map((j) => (
                      <option key={j.job_id} value={j.job_id}>
                        FY {j.fiscal_year}
                        {j.completed_at ? ` · ${j.completed_at.slice(0, 10)}` : ''}
                      </option>
                    ))}
                  </select>
                )
              ) : (
                <>
                  <input
                    type="file"
                    accept="application/pdf,.pdf"
                    disabled={uploading}
                    className="text-sm"
                    onChange={(e) => {
                      void handleUpload(e.target.files?.[0] ?? null);
                      e.target.value = '';
                    }}
                  />
                  {hasUpload && (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => void handleClearUpload()}
                    >
                      Clear upload
                    </Button>
                  )}
                </>
              )}
            </div>
            <div className="min-h-0 flex-1">
              {pdfStatus === 'loading' && (
                <p className="p-4 text-sm text-[#666666]">Loading PDF…</p>
              )}
              {pdfStatus === 'error' && (
                <p className="p-4 text-sm text-red-600">Could not load reference PDF.</p>
              )}
              {pdfStatus === 'ready' && pdfObjectUrl && (
                <iframe
                  title="Reference PDF"
                  src={pdfObjectUrl}
                  className="h-full w-full border-0"
                />
              )}
              {pdfStatus === 'idle' && (
                <p className="p-4 text-sm text-[#666666]">
                  Select a package or upload a PDF to compare against.
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      {inspecting && (
        <ChipInspector
          target={inspecting}
          value={chipValues?.values[inspecting.chip.id]}
          valuesLoaded={chipValues !== null}
          unavailableReason={chipValues?.unavailable_reason ?? null}
          onEdit={handleEditSetting}
          onClose={() => setInspecting(null)}
        />
      )}
    </div>
  );
}
