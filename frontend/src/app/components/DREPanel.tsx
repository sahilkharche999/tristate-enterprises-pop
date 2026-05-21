// DRE upload + list panel (Phase 3.1 + Phase 4.1).
// Lists every DRE document and extraction run for the HOA. Provides
// upload form for new DRE PDFs, a "Run extraction" button per uploaded
// document, and a "Review →" link to each run's Review Workbench page.

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  type DREDocument,
  type DREExtractionRunListItem,
  listDREDocuments,
  listExtractionRuns,
  triggerDREExtraction,
  uploadDRE,
} from '../api/dre';
import { DREReviewWorkbench } from './DREReviewWorkbench';

type Props = {
  hoaId: number;
};

const EXTRACTION_POLL_MS = 8000;
const EXTRACTION_POLL_TIMEOUT_MS = 5 * 60 * 1000;

const REVIEW_STATUS_COLORS: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-700',
  in_review: 'bg-blue-100 text-blue-800',
  promoted: 'bg-green-100 text-green-800',
  rejected: 'bg-red-100 text-red-800',
};

export function DREPanel({ hoaId }: Props) {
  const [docs, setDocs] = useState<DREDocument[]>([]);
  const [runs, setRuns] = useState<DREExtractionRunListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  // Documents the operator just scheduled an extraction for; we show
  // an "Extracting…" badge on these rows and poll the runs list until
  // a new run appears or the timeout expires.
  const [extractingDocIds, setExtractingDocIds] = useState<Set<number>>(new Set());
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [d, r] = await Promise.all([
        listDREDocuments(hoaId),
        listExtractionRuns(hoaId),
      ]);
      setDocs(d);
      setRuns(r);
      setError(null);
      return r;
    } catch (exc) {
      setError(String(exc));
      return null;
    } finally {
      setLoading(false);
    }
  }, [hoaId]);

  useEffect(() => {
    setLoading(true);
    refresh();
  }, [refresh]);

  // Stop polling when no extractions are in-flight.
  useEffect(() => {
    if (extractingDocIds.size === 0 && pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, [extractingDocIds]);

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
      refresh();
    } catch (exc) {
      setError(String(exc));
    } finally {
      setUploading(false);
    }
  }

  async function onRunExtraction(documentId: number) {
    setError(null);
    setExtractingDocIds((prev) => new Set(prev).add(documentId));
    const scheduledAt = Date.now();
    try {
      await triggerDREExtraction(hoaId, documentId);
    } catch (exc) {
      setError(String(exc));
      setExtractingDocIds((prev) => {
        const next = new Set(prev);
        next.delete(documentId);
        return next;
      });
      return;
    }
    // Poll the runs list until we see a new run for this document,
    // or the timeout expires.
    const baseline = new Set(
      runs.filter((r) => r.dre_document_id === documentId).map((r) => r.extraction_run_id),
    );

    const tick = async () => {
      if (Date.now() - scheduledAt > EXTRACTION_POLL_TIMEOUT_MS) {
        setExtractingDocIds((prev) => {
          const next = new Set(prev);
          next.delete(documentId);
          return next;
        });
        setError(
          `Extraction for document ${documentId} timed out after 5 minutes. ` +
            'Check backend logs.',
        );
        return;
      }
      const latest = await refresh();
      const newRun = (latest || []).find(
        (r) => r.dre_document_id === documentId && !baseline.has(r.extraction_run_id),
      );
      if (newRun) {
        setExtractingDocIds((prev) => {
          const next = new Set(prev);
          next.delete(documentId);
          return next;
        });
        return;
      }
      pollTimer.current = setTimeout(tick, EXTRACTION_POLL_MS);
    };
    pollTimer.current = setTimeout(tick, EXTRACTION_POLL_MS);
  }

  if (selectedRunId !== null) {
    return (
      <div className="space-y-2">
        <button
          type="button"
          onClick={() => setSelectedRunId(null)}
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
      <header>
        <h2 className="text-lg font-semibold">DRE documents & extractions</h2>
        <p className="text-sm text-gray-600">
          Upload a DRE PDF to extract its assessment setup via Gemini Vision.
          Review the extraction in the Workbench, then approve to promote
          it into a live AssessmentSetup.
        </p>
      </header>

      {error && (
        <div className="rounded border border-red-300 bg-red-50 p-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <section>
        <h3 className="mb-1 text-sm font-medium">Uploaded DRE documents</h3>
        {docs.length === 0 ? (
          <p className="text-sm text-gray-500">No DREs uploaded yet.</p>
        ) : (
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b text-left">
                <th>ID</th>
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
                const extracting = extractingDocIds.has(d.document_id);
                return (
                  <tr key={d.document_id} className="border-b">
                    <td>{d.document_id}</td>
                    <td className="font-mono">{d.file_name}</td>
                    <td>{d.page_count ?? '—'}</td>
                    <td>
                      <span className="rounded bg-gray-100 px-2 py-0.5 text-xs">
                        {d.status}
                      </span>
                    </td>
                    <td>{d.uploaded_at.slice(0, 10)}</td>
                    <td className="text-gray-500">{d.uploaded_by || '—'}</td>
                    <td>
                      <button
                        type="button"
                        disabled={extracting || d.status !== 'active'}
                        onClick={() => onRunExtraction(d.document_id)}
                        className="rounded border border-blue-400 px-2 py-0.5 text-xs text-blue-700 hover:bg-blue-50 disabled:opacity-50"
                      >
                        {extracting ? 'Extracting…' : 'Run extraction'}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h3 className="mb-1 text-sm font-medium">Extraction runs</h3>
        {runs.length === 0 ? (
          <p className="text-sm text-gray-500">
            No extractions yet. Click <strong>Run extraction</strong> on an
            uploaded DRE row above to kick off a Gemini Vision extraction
            (typically ~30–90 seconds).
          </p>
        ) : (
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b text-left">
                <th>Run ID</th>
                <th>Doc</th>
                <th>Status</th>
                <th>Review</th>
                <th>Promoted setup</th>
                <th>Completed</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.extraction_run_id} className="border-b">
                  <td>{r.extraction_run_id}</td>
                  <td>{r.dre_document_id}</td>
                  <td className="font-mono text-xs">{r.status}</td>
                  <td>
                    <span
                      className={`inline-block rounded px-2 py-0.5 text-xs ${
                        REVIEW_STATUS_COLORS[r.review_status] ||
                        'bg-gray-100 text-gray-700'
                      }`}
                    >
                      {r.review_status}
                    </span>
                  </td>
                  <td>{r.promoted_setup_id ?? '—'}</td>
                  <td className="text-gray-500">
                    {r.completed_at?.slice(0, 10) || '—'}
                  </td>
                  <td>
                    <button
                      type="button"
                      onClick={() => setSelectedRunId(r.extraction_run_id)}
                      className="rounded border px-2 py-0.5 text-xs hover:bg-gray-50"
                    >
                      Review →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <form
        onSubmit={onUpload}
        className="flex items-end gap-3 rounded border bg-gray-50 p-3"
      >
        <label className="flex-1">
          <span className="block text-sm">Upload new DRE PDF</span>
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
          />
        </label>
        <button
          type="submit"
          disabled={!uploadFile || uploading}
          className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {uploading ? 'Uploading…' : 'Upload'}
        </button>
      </form>
    </section>
  );
}
