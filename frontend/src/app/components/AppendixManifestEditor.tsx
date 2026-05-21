// Per-HOA appendix manifest editor (Phase 5.4 task 157 of
// dre-driven-assessment-engine). Lists every persistent appendix for an HOA
// with controls for: display title, include-by-default toggle, order arrows,
// cadence (persistent | annual | one_time), retire button. Upload form
// supports the cadence picker + annual_year + valid_through_year.

import { useEffect, useState } from 'react';
import {
  type AppendixCadence,
  type AppendixDocument,
  listAppendices,
  retireAppendix,
  updateAppendix,
  uploadAppendix,
} from '../api/appendices';

type Props = {
  hoaId: number;
};

const CADENCE_LABEL: Record<AppendixCadence, string> = {
  persistent: 'Persistent',
  annual: 'Annual',
  one_time: 'One-time',
};

export function AppendixManifestEditor({ hoaId }: Props) {
  const [appendices, setAppendices] = useState<AppendixDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [includeRetired, setIncludeRetired] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState('');
  const [uploadCadence, setUploadCadence] = useState<AppendixCadence>('persistent');
  const [uploadAnnualYear, setUploadAnnualYear] = useState<string>('');
  const [uploadValidThrough, setUploadValidThrough] = useState<string>('');
  const [uploading, setUploading] = useState(false);

  async function refresh() {
    try {
      const list = await listAppendices(hoaId, { includeRetired });
      setAppendices(list);
      setError(null);
    } catch (exc) {
      setError(String(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setLoading(true);
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hoaId, includeRetired]);

  async function onToggleIncludeDefault(row: AppendixDocument, next: boolean) {
    try {
      await updateAppendix(hoaId, row.appendix_id, {
        expected_version: row.version_int,
        include_by_default: next,
      });
      refresh();
    } catch (exc) {
      setError(String(exc));
    }
  }

  async function onChangeCadence(row: AppendixDocument, next: AppendixCadence) {
    try {
      await updateAppendix(hoaId, row.appendix_id, {
        expected_version: row.version_int,
        cadence: next,
        needs_cadence_review: false,
      });
      refresh();
    } catch (exc) {
      setError(String(exc));
    }
  }

  async function onReorder(row: AppendixDocument, direction: 'up' | 'down') {
    const sorted = [...appendices].sort(
      (a, b) => a.default_display_order - b.default_display_order || a.appendix_id - b.appendix_id,
    );
    const idx = sorted.findIndex((a) => a.appendix_id === row.appendix_id);
    const neighbor = direction === 'up' ? sorted[idx - 1] : sorted[idx + 1];
    if (!neighbor) return;
    try {
      await Promise.all([
        updateAppendix(hoaId, row.appendix_id, {
          expected_version: row.version_int,
          default_display_order: neighbor.default_display_order,
        }),
        updateAppendix(hoaId, neighbor.appendix_id, {
          expected_version: neighbor.version_int,
          default_display_order: row.default_display_order,
        }),
      ]);
      refresh();
    } catch (exc) {
      setError(String(exc));
    }
  }

  async function onRetire(row: AppendixDocument) {
    if (!confirm(`Retire "${row.display_title}"? Prior packages will still reference it.`)) {
      return;
    }
    try {
      await retireAppendix(hoaId, row.appendix_id);
      refresh();
    } catch (exc) {
      setError(String(exc));
    }
  }

  async function onUpload(event: React.FormEvent) {
    event.preventDefault();
    if (!uploadFile || !uploadTitle) {
      setError('Pick a PDF and enter a display title.');
      return;
    }
    setUploading(true);
    try {
      await uploadAppendix(hoaId, {
        file: uploadFile,
        displayTitle: uploadTitle,
        cadence: uploadCadence,
        annualYear: uploadAnnualYear ? parseInt(uploadAnnualYear, 10) : null,
        validThroughYear: uploadValidThrough ? parseInt(uploadValidThrough, 10) : null,
      });
      setUploadFile(null);
      setUploadTitle('');
      setUploadAnnualYear('');
      setUploadValidThrough('');
      setUploadCadence('persistent');
      refresh();
    } catch (exc) {
      setError(String(exc));
    } finally {
      setUploading(false);
    }
  }

  if (loading) return <div className="p-4 text-gray-500">Loading appendices…</div>;

  return (
    <section className="space-y-4 p-4">
      <header className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Disclosure-package appendices</h2>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={includeRetired}
            onChange={(e) => setIncludeRetired(e.target.checked)}
          />
          Show retired
        </label>
      </header>

      {error && (
        <div className="rounded border border-red-300 bg-red-50 p-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th className="py-1">#</th>
            <th>Display title</th>
            <th>File</th>
            <th>Include by default</th>
            <th>Cadence</th>
            <th>Status</th>
            <th>Reorder</th>
            <th>Retire</th>
          </tr>
        </thead>
        <tbody>
          {appendices.length === 0 && (
            <tr>
              <td colSpan={8} className="py-4 text-center text-gray-500">
                No appendices uploaded yet.
              </td>
            </tr>
          )}
          {appendices.map((row) => (
            <tr key={row.appendix_id} className="border-b">
              <td className="py-1">{row.default_display_order}</td>
              <td>
                <span className="font-medium">{row.display_title}</span>
                {row.needs_cadence_review && (
                  <span className="ml-2 inline-block rounded bg-yellow-100 px-1 text-xs text-yellow-800">
                    Cadence?
                  </span>
                )}
              </td>
              <td className="text-gray-600">{row.file_name}</td>
              <td>
                <input
                  type="checkbox"
                  checked={row.include_by_default}
                  disabled={row.status === 'retired'}
                  onChange={(e) => onToggleIncludeDefault(row, e.target.checked)}
                />
              </td>
              <td>
                <select
                  value={row.cadence}
                  disabled={row.status === 'retired'}
                  onChange={(e) =>
                    onChangeCadence(row, e.target.value as AppendixCadence)
                  }
                >
                  {(Object.keys(CADENCE_LABEL) as AppendixCadence[]).map((c) => (
                    <option key={c} value={c}>
                      {CADENCE_LABEL[c]}
                    </option>
                  ))}
                </select>
                {row.cadence === 'annual' && (
                  <span className="ml-2 text-xs text-gray-500">
                    yr {row.annual_year ?? '—'} / valid thru {row.valid_through_year ?? '—'}
                  </span>
                )}
              </td>
              <td>{row.status}</td>
              <td className="space-x-1">
                <button
                  type="button"
                  disabled={row.status === 'retired'}
                  onClick={() => onReorder(row, 'up')}
                  className="rounded border px-1 py-0.5 text-xs"
                >
                  ↑
                </button>
                <button
                  type="button"
                  disabled={row.status === 'retired'}
                  onClick={() => onReorder(row, 'down')}
                  className="rounded border px-1 py-0.5 text-xs"
                >
                  ↓
                </button>
              </td>
              <td>
                {row.status === 'active' ? (
                  <button
                    type="button"
                    onClick={() => onRetire(row)}
                    className="rounded border border-red-300 px-2 py-0.5 text-xs text-red-700 hover:bg-red-50"
                  >
                    Retire
                  </button>
                ) : (
                  <span className="text-xs text-gray-400">retired</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <form onSubmit={onUpload} className="space-y-2 rounded border bg-gray-50 p-3">
        <h3 className="font-medium">Upload new appendix</h3>
        <div className="grid grid-cols-2 gap-2 text-sm">
          <label>
            PDF:
            <input
              type="file"
              accept="application/pdf"
              onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <label>
            Display title:
            <input
              className="w-full rounded border px-2 py-1"
              value={uploadTitle}
              onChange={(e) => setUploadTitle(e.target.value)}
              placeholder="e.g. ADR / IDR Policy"
            />
          </label>
          <label>
            Cadence:
            <select
              className="w-full rounded border px-2 py-1"
              value={uploadCadence}
              onChange={(e) => setUploadCadence(e.target.value as AppendixCadence)}
            >
              {(Object.keys(CADENCE_LABEL) as AppendixCadence[]).map((c) => (
                <option key={c} value={c}>
                  {CADENCE_LABEL[c]}
                </option>
              ))}
            </select>
          </label>
          {uploadCadence === 'annual' && (
            <>
              <label>
                Annual year:
                <input
                  className="w-full rounded border px-2 py-1"
                  value={uploadAnnualYear}
                  onChange={(e) => setUploadAnnualYear(e.target.value)}
                  placeholder="2026"
                />
              </label>
              <label>
                Valid through year:
                <input
                  className="w-full rounded border px-2 py-1"
                  value={uploadValidThrough}
                  onChange={(e) => setUploadValidThrough(e.target.value)}
                  placeholder="2027"
                />
              </label>
            </>
          )}
        </div>
        <button
          type="submit"
          disabled={uploading || !uploadFile || !uploadTitle}
          className="rounded bg-blue-600 px-3 py-1 text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {uploading ? 'Uploading…' : 'Upload appendix'}
        </button>
      </form>
    </section>
  );
}
