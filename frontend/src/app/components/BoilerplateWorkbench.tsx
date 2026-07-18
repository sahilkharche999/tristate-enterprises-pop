/**
 * Full-screen package language workbench (hoa-boilerplate-workbench).
 *
 * Same shell pattern as DrePdfCompareView / DRE review compare:
 * fixed inset-0 overlay, left editor, right reference PDF, Close to dismiss.
 *
 * Reference PDF: prior app-generated job for this HOA, or upload.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import {
  type BoilerplateReferenceJob,
  type BoilerplateSlot,
  boilerplateReferencePdfUrl,
  deleteBoilerplateReferencePdf,
  getBoilerplateSettings,
  listBoilerplateReferenceJobs,
  putBoilerplateSettings,
  uploadBoilerplateReferencePdf,
} from '../api/hoaSettings';
import { authHeaders } from '../api/http';
import { getErrorMessage } from '../lib/errors';
import { Button } from './ui/button';
import { clampSplitPercent } from './resizableSplitPane';
import { toast } from 'sonner';

const SLOT_ID = 'cover_letter_body';

type ReferenceSource = 'job' | 'upload';

export function BoilerplateWorkbench({
  hoaId,
  open,
  onClose,
  hoaName,
}: {
  hoaId: number;
  open: boolean;
  onClose: () => void;
  hoaName?: string;
}) {
  const [slots, setSlots] = useState<BoilerplateSlot[]>([]);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [referenceSource, setReferenceSource] = useState<ReferenceSource>('job');
  const [jobs, setJobs] = useState<BoilerplateReferenceJob[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string>('');
  const [hasUpload, setHasUpload] = useState(false);
  const [pdfObjectUrl, setPdfObjectUrl] = useState<string | null>(null);
  const [pdfStatus, setPdfStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [uploading, setUploading] = useState(false);
  const [leftWidthPercent, setLeftWidthPercent] = useState(50);
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getBoilerplateSettings(hoaId);
      setSlots(data.slots);
      const cover = data.slots.find((s) => s.id === SLOT_ID);
      setDraft(cover?.value ?? '');
      setHasUpload(Boolean(data.has_reference_upload));
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to load package language.'));
    } finally {
      setLoading(false);
    }
  }, [hoaId]);

  // Re-fetch every time the full-screen workbench opens (leave → edit budget → return).
  useEffect(() => {
    if (!open) return;
    void load();
  }, [open, load]);

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
    if (!open) {
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
      setPdfObjectUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
      return;
    }

    let url: string | null = null;
    let cancelled = false;
    setPdfStatus('loading');
    const fileUrl = boilerplateReferencePdfUrl(
      hoaId,
      referenceSource,
      selectedJobId || undefined,
    );
    void fetch(fileUrl, { headers: authHeaders() })
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
  }, [hoaId, open, referenceSource, selectedJobId, hasUpload]);

  // Escape closes full-screen workbench
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const next = await putBoilerplateSettings(hoaId, {
        [SLOT_ID]: draft.trim() ? draft : null,
      });
      setSlots(next.slots);
      const cover = next.slots.find((s) => s.id === SLOT_ID);
      setDraft(cover?.value ?? '');
      toast.success('Package language saved for this HOA.');
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to save package language.'));
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    setSaving(true);
    try {
      const next = await putBoilerplateSettings(hoaId, { [SLOT_ID]: null });
      setSlots(next.slots);
      setDraft('');
      toast.success('Reset to system-generated wording.');
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

  const handleDividerPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    setIsDragging(true);
  };

  const handleDividerPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!isDragging || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const percent = ((event.clientX - rect.left) / rect.width) * 100;
    setLeftWidthPercent(clampSplitPercent(percent));
  };

  const stopDragging = (event: React.PointerEvent<HTMLDivElement>) => {
    event.currentTarget.releasePointerCapture(event.pointerId);
    setIsDragging(false);
  };

  if (!open) return null;

  const slotLabel = slots.find((s) => s.id === SLOT_ID)?.label ?? 'Cover letter intro';
  const isOverride = Boolean(slots.find((s) => s.id === SLOT_ID)?.is_override);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-label="Package language workbench"
    >
      <div className="flex items-center justify-between border-b border-[#e5e5e5] bg-white px-4 py-2.5">
        <div className="min-w-0">
          <p className="text-sm font-medium text-[#111111]">
            Package language{hoaName ? ` · ${hoaName}` : ''}
          </p>
          <p className="text-xs text-[#737373]">
            Edit cover-letter intro for this HOA · reference PDF on the right · Esc or ✕ to close
          </p>
        </div>
        <Button type="button" variant="ghost" onClick={onClose} aria-label="Close package language workbench">
          <X className="h-5 w-5 text-[#525252]" />
        </Button>
      </div>

      <div ref={containerRef} className="flex min-h-0 flex-1">
        {/* Left: editor */}
        <div
          className="flex min-h-0 shrink-0 flex-col overflow-auto bg-white p-5"
          style={{ width: `${leftWidthPercent}%` }}
        >
          {loading ? (
            <p className="text-sm text-[#666666]">Loading package language…</p>
          ) : (
            <>
              <p className="mb-4 text-sm text-[#666666]">
                Saves apply to <strong>this HOA only</strong> for future packages. Leave blank for
                system-generated wording (includes computed assessment figures). Numbers are not
                edited here — save, then generate the disclosure package.
              </p>
              <label className="mb-1 block text-sm font-medium text-[#1a1a1a]" htmlFor="bp-cover-fs">
                {slotLabel}
                {isOverride ? (
                  <span className="ml-2 text-xs font-normal text-[#0a7]">Custom for this HOA</span>
                ) : (
                  <span className="ml-2 text-xs font-normal text-[#888]">Using system default</span>
                )}
              </label>
              <textarea
                id="bp-cover-fs"
                className="min-h-[min(50vh,360px)] w-full flex-1 rounded-md border border-[#d4d4d4] px-3 py-2 text-sm text-[#1a1a1a] focus:outline-none focus:ring-2 focus:ring-[#1a1a1a]"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Leave blank for system-generated cover letter intro…"
              />
              <div className="mt-4 flex flex-wrap gap-2">
                <Button type="button" onClick={() => void handleSave()} disabled={saving}>
                  {saving ? 'Saving…' : 'Save for this HOA'}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void handleReset()}
                  disabled={saving || !isOverride}
                >
                  Reset to system default
                </Button>
                <Button type="button" variant="outline" onClick={onClose}>
                  Close
                </Button>
              </div>
            </>
          )}
        </div>

        {/* Resize divider */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize panels"
          tabIndex={0}
          onPointerDown={handleDividerPointerDown}
          onPointerMove={handleDividerPointerMove}
          onPointerUp={stopDragging}
          onPointerCancel={stopDragging}
          className={`group relative w-2 shrink-0 cursor-col-resize bg-[#e5e5e5] transition-colors hover:bg-[#a3a3a3] ${
            isDragging ? 'bg-[#737373]' : ''
          }`}
        >
          <div className="absolute inset-y-0 left-1/2 w-4 -translate-x-1/2" />
        </div>

        {/* Right: reference PDF (always visible in full-screen workbench) */}
        <div className="flex min-h-0 flex-1 flex-col bg-[#fafafa]">
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
                <span className="text-xs text-[#666666]">No completed packages — try Upload</span>
              ) : (
                <select
                  className="max-w-[min(100%,280px)] rounded border border-[#d4d4d4] px-2 py-1 text-sm"
                  value={selectedJobId}
                  onChange={(e) => setSelectedJobId(e.target.value)}
                >
                  {jobs.map((j) => (
                    <option key={j.job_id} value={j.job_id}>
                      FY {j.fiscal_year}
                      {j.completed_at ? ` · ${j.completed_at.slice(0, 10)}` : ''}
                      {j.annual_package_id != null ? ` · pkg #${j.annual_package_id}` : ''}
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
                    const f = e.target.files?.[0] ?? null;
                    void handleUpload(f);
                    e.target.value = '';
                  }}
                />
                {hasUpload && (
                  <Button type="button" variant="outline" onClick={() => void handleClearUpload()}>
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
              <iframe title="Reference PDF" src={pdfObjectUrl} className="h-full w-full border-0" />
            )}
            {pdfStatus === 'idle' && (
              <p className="p-4 text-sm text-[#666666]">
                Select a package or upload a PDF. Select text in the viewer and paste into the editor
                on the left.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
