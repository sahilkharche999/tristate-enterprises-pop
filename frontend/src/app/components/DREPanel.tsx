// DRE settings section panel — tabbed container with two sub-tabs:
//   "DRE Documents"      — upload DRE PDFs, run extraction, review & promote.
//   "CC&R / Governing Docs" — upload CC&R PDFs, run extraction, enter per-unit
//                             factors, and promote into a live AssessmentSetup.
// Both sub-tabs live inside the existing value="dre" settings section.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  type DREDocument,
  type DREExtractionRunListItem,
  listDREDocuments,
  listExtractionRuns,
  triggerDREExtraction,
  uploadDRE,
} from '../api/dre';
import { FileDropzone } from './fileDropzone';
import { DREReviewWorkbench } from './DREReviewWorkbench';
import { CCRPanel } from './CCRPanel';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './ui/tabs';

type Props = {
  hoaId: number;
};

const EXTRACTION_POLL_MS = 2000;
const MAX_POLL_FAILURES = 3;
const ACTIVE_JOB_STATUSES = new Set(['queued', 'running']);

const REVIEW_STATUS_COLORS: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-700',
  approved: 'bg-blue-100 text-blue-800',
  promoted: 'bg-green-100 text-green-800',
  rejected: 'bg-red-100 text-red-800',
};

function isActiveRun(run: DREExtractionRunListItem): boolean {
  return ACTIVE_JOB_STATUSES.has(run.job_status);
}

function lifecycleLabel(run: DREExtractionRunListItem): string {
  if (run.job_status === 'queued') return 'Queued';
  if (run.job_status === 'running') return 'Running';
  if (run.job_status === 'failed') return 'Job failed';
  if (run.status === 'succeeded') return 'Succeeded';
  if (run.status === 'extraction_partial') return 'Partial';
  return 'Failed';
}

function lifecycleTone(run: DREExtractionRunListItem): string {
  if (run.job_status === 'queued') return 'bg-amber-100 text-amber-800';
  if (run.job_status === 'running') return 'bg-blue-100 text-blue-800';
  if (run.job_status === 'failed') return 'bg-rose-100 text-rose-800';
  if (run.status === 'succeeded') return 'bg-emerald-100 text-emerald-800';
  if (run.status === 'extraction_partial') return 'bg-amber-100 text-amber-800';
  return 'bg-rose-100 text-rose-800';
}

function DREDocumentsTab({ hoaId }: Props) {
  const [docs, setDocs] = useState<DREDocument[]>([]);
  const [runs, setRuns] = useState<DREExtractionRunListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollFailureCount = useRef(0);

  const fetchState = useCallback(async () => {
    return Promise.all([
      listDREDocuments(hoaId),
      listExtractionRuns(hoaId),
    ]);
  }, [hoaId]);

  const refresh = useCallback(async () => {
    try {
      const [d, r] = await fetchState();
      setDocs(d);
      setRuns(r);
      setError(null);
      pollFailureCount.current = 0;
      return r;
    } catch (exc) {
      setError(String(exc));
      return null;
    } finally {
      setLoading(false);
    }
  }, [fetchState]);

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, [refresh]);

  const activeRunByDocumentId = useMemo(() => {
    const byDocumentId = new Map<number, DREExtractionRunListItem>();
    runs.forEach((run) => {
      if (isActiveRun(run) && !byDocumentId.has(run.dre_document_id)) {
        byDocumentId.set(run.dre_document_id, run);
      }
    });
    return byDocumentId;
  }, [runs]);

  const shouldPoll = selectedRunId === null && runs.some(isActiveRun);

  useEffect(() => {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
    if (!shouldPoll) return;

    let cancelled = false;

    const tick = async () => {
      try {
        const [d, r] = await fetchState();
        if (cancelled) return;
        setDocs(d);
        setRuns(r);
        setError(null);
        pollFailureCount.current = 0;
        if (!r.some(isActiveRun)) {
          pollTimer.current = null;
          return;
        }
      } catch {
        if (cancelled) return;
        pollFailureCount.current += 1;
        if (pollFailureCount.current >= MAX_POLL_FAILURES) {
          setError('Lost connection to extraction status. Refresh the page to check current state.');
          pollTimer.current = null;
          return;
        }
      }
      pollTimer.current = setTimeout(tick, EXTRACTION_POLL_MS);
    };

    pollTimer.current = setTimeout(tick, EXTRACTION_POLL_MS);
    return () => {
      cancelled = true;
      if (pollTimer.current) {
        clearTimeout(pollTimer.current);
        pollTimer.current = null;
      }
    };
  }, [fetchState, shouldPoll]);

  async function onUpload(event: React.FormEvent) {
    event.preventDefault();
    if (!uploadFile) {
      setError('Pick a PDF first.');
      return;
    }
    setUploading(true);
    try {
      await uploadDRE(hoaId, uploadFile);
      setUploadFile(null);
      await refresh();
    } catch (exc) {
      setError(String(exc));
    } finally {
      setUploading(false);
    }
  }

  async function onRunExtraction(documentId: number) {
    setError(null);
    try {
      await triggerDREExtraction(hoaId, documentId);
      await refresh();
    } catch (exc) {
      setError(String(exc));
    }
  }

  if (selectedRunId !== null) {
    return (
      <div className="space-y-2">
        <button
          type="button"
          onClick={() => {
            setSelectedRunId(null);
            void refresh();
          }}
          className="rounded border px-2 py-1 text-sm hover:bg-gray-50"
        >
          ← Back to DRE list
        </button>
        <DREReviewWorkbench hoaId={hoaId} runId={selectedRunId} />
      </div>
    );
  }

  if (loading) return <div className="p-4 text-gray-500">Loading DRE state…</div>;

  return (
    <section className="space-y-4 p-4">
      <header className="space-y-1">
        <h2 className="text-lg font-semibold text-[#111111]">DRE documents & extractions</h2>
        <p className="text-sm text-[#525252]">
          Upload a DRE PDF to extract its assessment setup via Gemini Vision.
          Review the extraction in the Workbench, then approve to promote
          it into a live AssessmentSetup.
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <section className="rounded-xl border border-[#e5e5e5] bg-white p-4 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold text-[#111111]">Uploaded DRE documents</h3>
        {docs.length === 0 ? (
          <p className="rounded-lg border border-dashed border-[#d4d4d4] bg-[#fafafa] px-3 py-4 text-sm text-[#737373]">
            No DREs uploaded yet.
          </p>
        ) : (
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-[#e5e5e5] text-left text-xs uppercase tracking-[0.08em] text-[#737373]">
                <th className="py-2">ID</th>
                <th>File</th>
                <th>Pages</th>
                <th>Status</th>
                <th>Uploaded</th>
                <th>By</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => {
                const activeRun = activeRunByDocumentId.get(d.document_id);
                const busy = Boolean(activeRun);
                const buttonLabel =
                  activeRun?.job_status === 'queued'
                    ? 'Queued…'
                    : activeRun?.job_status === 'running'
                      ? 'Running…'
                      : 'Run extraction';
                return (
                  <tr key={d.document_id} className="border-b border-[#f0f0f0] transition-colors hover:bg-[#fafafa]">
                    <td className="py-3">{d.document_id}</td>
                    <td className="font-mono">{d.file_name}</td>
                    <td>{d.page_count ?? '—'}</td>
                    <td>
                      <span className="rounded-full bg-[#f5f5f5] px-2 py-0.5 text-xs text-[#525252]">
                        {d.status}
                      </span>
                    </td>
                    <td>{d.uploaded_at.slice(0, 10)}</td>
                    <td className="text-[#737373]">{d.uploaded_by || '—'}</td>
                    <td>
                      <button
                        type="button"
                        disabled={busy || d.status !== 'active'}
                        onClick={() => onRunExtraction(d.document_id)}
                        className="rounded-md border border-[#d4d4d4] px-2 py-1 text-xs text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {buttonLabel}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <section className="rounded-xl border border-[#e5e5e5] bg-white p-4 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold text-[#111111]">Extraction runs</h3>
        {runs.length === 0 ? (
          <p className="rounded-lg border border-dashed border-[#d4d4d4] bg-[#fafafa] px-3 py-4 text-sm text-[#737373]">
            No extractions yet. Click <strong>Run extraction</strong> on an
            uploaded DRE row above to kick off a Gemini Vision extraction.
          </p>
        ) : (
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-[#e5e5e5] text-left text-xs uppercase tracking-[0.08em] text-[#737373]">
                <th className="py-2">Run ID</th>
                <th>Doc</th>
                <th>Status</th>
                <th>Review</th>
                <th>Promoted setup</th>
                <th>Started</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const active = isActiveRun(r);
                return (
                  <tr key={r.extraction_run_id} className="border-b border-[#f0f0f0] transition-colors hover:bg-[#fafafa]">
                    <td className="py-3">{r.extraction_run_id}</td>
                    <td>{r.dre_document_id}</td>
                    <td>
                      <div className="space-y-1">
                        <span
                          className={`inline-block rounded-full px-2 py-0.5 text-xs ${lifecycleTone(r)}`}
                        >
                          {lifecycleLabel(r)}
                        </span>
                        {r.error_message ? (
                          <div className="max-w-[320px] text-xs text-rose-700">
                            {r.error_message}
                          </div>
                        ) : null}
                      </div>
                    </td>
                    <td>
                      <span
                        className={`inline-block rounded-full px-2 py-0.5 text-xs ${
                          REVIEW_STATUS_COLORS[r.review_status] ||
                          'bg-gray-100 text-gray-700'
                        }`}
                      >
                        {r.review_status}
                      </span>
                    </td>
                    <td>{r.promoted_setup_id ?? '—'}</td>
                    <td className="text-gray-500">
                      {(r.started_at || '').slice(0, 19).replace('T', ' ') || '—'}
                    </td>
                    <td>
                      <button
                        type="button"
                        disabled={active}
                        onClick={() => setSelectedRunId(r.extraction_run_id)}
                        className="rounded-md border border-[#d4d4d4] px-2 py-1 text-xs text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {active ? 'Waiting…' : 'Review →'}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <form onSubmit={onUpload} className="flex items-end gap-3 rounded border border-[#e5e5e5] bg-[#fafafa] p-3">
        <div className="flex-1">
          <FileDropzone
            title="Upload new DRE PDF"
            helper="Pick the scanned DRE PDF that describes assessment setup."
            accept="application/pdf,.pdf"
            fileName={uploadFile?.name ?? null}
            disabled={uploading}
            status={uploadFile ? 'selected' : 'idle'}
            statusMessage={uploadFile ? 'File selected.' : 'Scanned PDF preferred.'}
            actionLabel="Choose PDF"
            onFilesSelected={(files) => setUploadFile(files?.[0] ?? null)}
            onClear={() => setUploadFile(null)}
          />
        </div>
        <button
          type="submit"
          disabled={!uploadFile || uploading}
          className="rounded-md bg-[#111111] px-3 py-2 text-sm text-white hover:bg-[#262626] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {uploading ? 'Uploading…' : 'Upload'}
        </button>
      </form>
    </section>
  );
}

export function DREPanel({ hoaId }: Props) {
  return (
    <Tabs defaultValue="dre-docs" className="w-full">
      <TabsList className="border-b border-[#e5e5e5] bg-transparent px-4 pt-3">
        <TabsTrigger
          value="dre-docs"
          className="rounded-t-md border border-b-0 border-[#e5e5e5] bg-white px-4 py-2 text-sm font-medium data-[state=inactive]:bg-[#f5f5f5] data-[state=inactive]:text-[#737373]"
        >
          DRE Documents
        </TabsTrigger>
        <TabsTrigger
          value="ccr-docs"
          className="rounded-t-md border border-b-0 border-[#e5e5e5] bg-white px-4 py-2 text-sm font-medium data-[state=inactive]:bg-[#f5f5f5] data-[state=inactive]:text-[#737373]"
        >
          CC&R / Governing Docs
        </TabsTrigger>
      </TabsList>
      <TabsContent value="dre-docs">
        <DREDocumentsTab hoaId={hoaId} />
      </TabsContent>
      <TabsContent value="ccr-docs">
        <CCRPanel hoaId={hoaId} />
      </TabsContent>
    </Tabs>
  );
}
