// CC&R / governing-document upload, extraction, and per-unit factor panel.
// Mirrors the DRE Documents lifecycle: upload PDF → run extraction → review →
// enter per-unit factors → approve/promote.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type {
  DREDocument,
  DREExtractionRunListItem,
} from '../api/dre';
import {
  listCCRDocuments,
  listCCRExtractionRuns,
  triggerCCRExtraction,
  uploadCCR,
  getCCRUnitFactors,
  saveCCRUnitFactors,
  approveCCRRun,
  demoteCCRRun,
  type CCRUnitFactorEntry,
} from '../api/ccr';
import { getExtractionRun } from '../api/dre';
import { FileDropzone } from './fileDropzone';
import { DREReviewWorkbench } from './DREReviewWorkbench';
import { AllocationResolutionPanel } from './AllocationResolutionPanel';

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

// ── Per-unit factor editor ──────────────────────────────────────────────────

type FactorMap = Record<string, { square_feet?: number; ownership_percent?: number }>;

function FactorEditor({
  runId,
  hoaId,
  onClose,
}: {
  runId: number;
  hoaId: number;
  onClose: () => void;
}) {
  const [factors, setFactors] = useState<FactorMap>({});
  const [newUnit, setNewUnit] = useState('');
  const [newSqFt, setNewSqFt] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<{ human_review_questions?: unknown[] } | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const [fetchedFactors, runDetail] = await Promise.all([
          getCCRUnitFactors(hoaId, runId),
          getExtractionRun(hoaId, runId),
        ]);
        setFactors(fetchedFactors);
        setRun(runDetail as typeof run);
      } catch (exc) {
        setError(String(exc));
      }
    })();
  }, [hoaId, runId]);

  function addUnit() {
    if (!newUnit.trim()) return;
    setFactors((prev) => ({
      ...prev,
      [newUnit.trim()]: { square_feet: newSqFt ? Number(newSqFt) : undefined },
    }));
    setNewUnit('');
    setNewSqFt('');
  }

  function updateFactor(unit: string, field: 'square_feet' | 'ownership_percent', value: string) {
    setFactors((prev) => ({
      ...prev,
      [unit]: { ...prev[unit], [field]: value ? Number(value) : undefined },
    }));
  }

  async function onSave() {
    setSaving(true);
    setError(null);
    try {
      const entries: CCRUnitFactorEntry[] = Object.entries(factors).map(([unit_number, f]) => ({
        unit_number,
        square_feet: f.square_feet ?? null,
        ownership_percent: f.ownership_percent ?? null,
      }));
      await saveCCRUnitFactors(hoaId, runId, entries);
      onClose();
    } catch (exc) {
      setError(String(exc));
    } finally {
      setSaving(false);
    }
  }

  const reviewQuestions = (run as { parsed_json?: { human_review_questions?: Array<{ question: string; reason: string; severity: string; source_pages?: number[] }> } } | null)
    ?.parsed_json?.human_review_questions ?? [];

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[#111111]">Per-unit allocation factors</h3>
        <button
          type="button"
          onClick={onClose}
          className="rounded border px-2 py-1 text-sm hover:bg-gray-50"
        >
          ← Back
        </button>
      </div>

      <p className="text-xs text-[#737373]">
        Enter per-unit square footage (or ownership percentage) for proportional
        pools. These values are used during promotion to populate the per-unit
        allocation rows.
      </p>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {reviewQuestions.length > 0 && (
        <section className="rounded-xl border border-amber-200 bg-amber-50 p-3">
          <h4 className="mb-2 text-xs font-semibold text-amber-800">Review questions from extraction</h4>
          <ul className="space-y-2">
            {reviewQuestions.map((q, i) => (
              <li key={i} className="text-xs text-amber-900">
                <span className={`mr-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
                  q.severity === 'high' ? 'bg-red-200 text-red-800' :
                  q.severity === 'medium' ? 'bg-amber-200 text-amber-800' :
                  'bg-gray-200 text-gray-700'
                }`}>
                  {q.severity}
                </span>
                <strong>{q.question}</strong>
                {q.reason && <span className="ml-1 text-amber-700">({q.reason})</span>}
                {q.source_pages && q.source_pages.length > 0 && (
                  <span className="ml-1 text-amber-600">pp. {q.source_pages.join(', ')}</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="rounded-xl border border-[#e5e5e5] bg-white p-4">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-[#e5e5e5] text-left text-xs uppercase tracking-[0.08em] text-[#737373]">
              <th className="py-2">Unit</th>
              <th>Sq Ft</th>
              <th>Ownership %</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(factors).map(([unit, f]) => (
              <tr key={unit} className="border-b border-[#f0f0f0]">
                <td className="py-2 font-mono text-xs">{unit}</td>
                <td>
                  <input
                    type="number"
                    value={f.square_feet ?? ''}
                    onChange={(e) => updateFactor(unit, 'square_feet', e.target.value)}
                    className="w-24 rounded border px-1 py-0.5 text-xs"
                    placeholder="—"
                  />
                </td>
                <td>
                  <input
                    type="number"
                    value={f.ownership_percent ?? ''}
                    onChange={(e) => updateFactor(unit, 'ownership_percent', e.target.value)}
                    className="w-24 rounded border px-1 py-0.5 text-xs"
                    placeholder="—"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {Object.keys(factors).length === 0 && (
          <p className="py-2 text-xs text-[#737373]">No units yet. Add units below.</p>
        )}
      </section>

      <div className="flex items-end gap-2 rounded border border-[#e5e5e5] bg-[#fafafa] p-3">
        <div>
          <label className="mb-1 block text-xs text-[#525252]">Unit number</label>
          <input
            type="text"
            value={newUnit}
            onChange={(e) => setNewUnit(e.target.value)}
            placeholder="e.g. 101"
            className="w-24 rounded border px-2 py-1 text-xs"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-[#525252]">Sq Ft</label>
          <input
            type="number"
            value={newSqFt}
            onChange={(e) => setNewSqFt(e.target.value)}
            placeholder="e.g. 850"
            className="w-24 rounded border px-2 py-1 text-xs"
          />
        </div>
        <button
          type="button"
          onClick={addUnit}
          className="rounded-md border border-[#d4d4d4] px-2 py-1.5 text-xs hover:bg-[#f5f5f5]"
        >
          Add unit
        </button>
      </div>

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => void onSave()}
          disabled={saving}
          className="rounded-md bg-[#111111] px-3 py-2 text-sm text-white hover:bg-[#262626] disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save factors'}
        </button>
        <button type="button" onClick={onClose} className="rounded-md border px-3 py-2 text-sm hover:bg-gray-50">
          Cancel
        </button>
      </div>
    </div>
  );
}

// ── Main CC&R panel ─────────────────────────────────────────────────────────

export function CCRPanel({ hoaId }: Props) {
  const [docs, setDocs] = useState<DREDocument[]>([]);
  const [runs, setRuns] = useState<DREExtractionRunListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [factorRunId, setFactorRunId] = useState<number | null>(null);
  const [approvingRunId, setApprovingRunId] = useState<number | null>(null);
  const [demotingRunId, setDemotingRunId] = useState<number | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollFailureCount = useRef(0);

  const fetchState = useCallback(async () => {
    return Promise.all([
      listCCRDocuments(hoaId),
      listCCRExtractionRuns(hoaId),
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

  const shouldPoll = selectedRunId === null && factorRunId === null && runs.some(isActiveRun);

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
          setError('Lost connection to extraction status. Refresh to check current state.');
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
    if (!uploadFile) { setError('Pick a PDF first.'); return; }
    setUploading(true);
    try {
      await uploadCCR(hoaId, uploadFile);
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
      await triggerCCRExtraction(hoaId, documentId);
      await refresh();
    } catch (exc) {
      setError(String(exc));
    }
  }

  async function onApprove(runId: number) {
    setApprovingRunId(runId);
    setError(null);
    try {
      await approveCCRRun(hoaId, runId, 'per_unit');
      await refresh();
    } catch (exc: unknown) {
      const detail = (exc as { detail?: { message?: string } })?.detail;
      if (detail && typeof detail === 'object' && detail.message) {
        setError(`${detail.message} — enter per-unit factors first.`);
        setFactorRunId(runId);
      } else {
        setError(String(exc));
      }
    } finally {
      setApprovingRunId(null);
    }
  }

  async function onDemote(runId: number) {
    if (!window.confirm(
      'Demote this promoted CC&R? Its assessment setup will be unseated and ' +
      'the prior setup (if any) restored. You can re-promote or switch to a ' +
      'different document afterward.',
    )) {
      return;
    }
    setDemotingRunId(runId);
    setError(null);
    try {
      await demoteCCRRun(hoaId, runId);
      await refresh();
    } catch (exc: unknown) {
      const detail = (exc as { detail?: { message?: string } })?.detail;
      if (detail && typeof detail === 'object' && detail.message) {
        setError(detail.message);
      } else {
        setError(String(exc));
      }
    } finally {
      setDemotingRunId(null);
    }
  }

  // Per-unit factor editor view
  if (factorRunId !== null) {
    return (
      <FactorEditor
        hoaId={hoaId}
        runId={factorRunId}
        onClose={() => {
          setFactorRunId(null);
          void refresh();
        }}
      />
    );
  }

  // Extraction review workbench view
  if (selectedRunId !== null) {
    return (
      <div className="space-y-2">
        <button
          type="button"
          onClick={() => { setSelectedRunId(null); void refresh(); }}
          className="rounded border px-2 py-1 text-sm hover:bg-gray-50"
        >
          ← Back to CC&R list
        </button>
        <DREReviewWorkbench hoaId={hoaId} runId={selectedRunId} />
      </div>
    );
  }

  if (loading) return <div className="p-4 text-gray-500">Loading CC&R state…</div>;

  return (
    <section className="space-y-4 p-4">
      <header className="space-y-1">
        <h2 className="text-lg font-semibold text-[#111111]">CC&R / Governing documents</h2>
        <p className="text-sm text-[#525252]">
          Upload a scanned CC&R or Declaration PDF to extract its assessment-allocation
          policy via Gemini Vision. Review the extraction, enter per-unit allocation
          factors, then promote to create a live AssessmentSetup.
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <section className="rounded-xl border border-[#e5e5e5] bg-white p-4 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold text-[#111111]">Uploaded governing documents</h3>
        {docs.length === 0 ? (
          <p className="rounded-lg border border-dashed border-[#d4d4d4] bg-[#fafafa] px-3 py-4 text-sm text-[#737373]">
            No CC&R documents uploaded yet.
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
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => {
                const activeRun = activeRunByDocumentId.get(d.document_id);
                const busy = Boolean(activeRun);
                const buttonLabel =
                  activeRun?.job_status === 'queued' ? 'Queued…' :
                  activeRun?.job_status === 'running' ? 'Running…' :
                  'Run extraction';
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
                    <td>
                      <button
                        type="button"
                        disabled={busy || d.status !== 'active'}
                        onClick={() => void onRunExtraction(d.document_id)}
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
            No extractions yet. Click <strong>Run extraction</strong> on an uploaded document above.
          </p>
        ) : (
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-[#e5e5e5] text-left text-xs uppercase tracking-[0.08em] text-[#737373]">
                <th className="py-2">Run</th>
                <th>Doc</th>
                <th>Status</th>
                <th>Review</th>
                <th>Promoted</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const active = isActiveRun(r);
                const canReview = !active && r.job_status !== 'failed';
                const canPromote = canReview && r.review_status !== 'promoted';
                const canDemote = !active && r.review_status === 'promoted';
                return (
                  <tr key={r.extraction_run_id} className="border-b border-[#f0f0f0] transition-colors hover:bg-[#fafafa]">
                    <td className="py-3">{r.extraction_run_id}</td>
                    <td>{r.dre_document_id}</td>
                    <td>
                      <div className="space-y-1">
                        <span className={`inline-block rounded-full px-2 py-0.5 text-xs ${lifecycleTone(r)}`}>
                          {lifecycleLabel(r)}
                        </span>
                        {r.error_message && (
                          <div className="max-w-[280px] text-xs text-rose-700">{r.error_message}</div>
                        )}
                      </div>
                    </td>
                    <td>
                      <span className={`inline-block rounded-full px-2 py-0.5 text-xs ${
                        REVIEW_STATUS_COLORS[r.review_status] || 'bg-gray-100 text-gray-700'
                      }`}>
                        {r.review_status}
                      </span>
                    </td>
                    <td>{r.promoted_setup_id ?? '—'}</td>
                    <td>
                      <div className="flex gap-1">
                        {active ? (
                          <span className="text-xs text-gray-400">Waiting…</span>
                        ) : (
                          <>
                            {canReview && (
                              <button
                                type="button"
                                onClick={() => setSelectedRunId(r.extraction_run_id)}
                                className="rounded-md border border-[#d4d4d4] px-2 py-1 text-xs hover:bg-[#f5f5f5]"
                              >
                                Review
                              </button>
                            )}
                            {canReview && (
                              <button
                                type="button"
                                onClick={() => setFactorRunId(r.extraction_run_id)}
                                className="rounded-md border border-[#d4d4d4] px-2 py-1 text-xs hover:bg-[#f5f5f5]"
                              >
                                Factors
                              </button>
                            )}
                            {canPromote && (
                              <button
                                type="button"
                                disabled={approvingRunId === r.extraction_run_id}
                                onClick={() => void onApprove(r.extraction_run_id)}
                                className="rounded-md bg-[#111111] px-2 py-1 text-xs text-white hover:bg-[#262626] disabled:opacity-50"
                              >
                                {approvingRunId === r.extraction_run_id ? 'Promoting…' : 'Promote'}
                              </button>
                            )}
                            {canDemote && (
                              <button
                                type="button"
                                disabled={demotingRunId === r.extraction_run_id}
                                onClick={() => void onDemote(r.extraction_run_id)}
                                className="rounded-md border border-rose-300 bg-rose-50 px-2 py-1 text-xs text-rose-700 hover:bg-rose-100 disabled:opacity-50"
                              >
                                {demotingRunId === r.extraction_run_id ? 'Demoting…' : 'Demote'}
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <form onSubmit={(e) => void onUpload(e)} className="flex items-end gap-3 rounded border border-[#e5e5e5] bg-[#fafafa] p-3">
        <div className="flex-1">
          <FileDropzone
            title="Upload CC&R / Governing document PDF"
            helper="Pick the scanned Declaration or CC&R PDF."
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

      <AllocationResolutionPanel hoaId={hoaId} />
    </section>
  );
}
