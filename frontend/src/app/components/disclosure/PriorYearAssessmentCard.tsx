// Prior-year final package card — feeds the YoY assessment schedule table.
// Primary source after year 1 is a finalized prior package (automatic).
// Year-1 bootstrap: upload last year's final PDF → extract draft → confirm.

import { useCallback, useEffect, useState } from 'react';

import {
  confirmPriorAssessmentSchedule,
  deletePriorAssessmentSchedule,
  extractPriorAssessmentSchedule,
  getPriorAssessmentSchedule,
  type PriorAssessmentStatusResponse,
  type PriorScheduleRow,
} from '../../api/annualPackages';
import { Button } from '../ui/button';
import { FileDropzone } from '../fileDropzone';

export interface PriorYearAssessmentCardProps {
  hoaId: number;
  fiscalYear: number;
  disabled?: boolean;
}

export function PriorYearAssessmentCard({
  hoaId,
  fiscalYear,
  disabled = false,
}: PriorYearAssessmentCardProps) {
  const [status, setStatus] = useState<PriorAssessmentStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draftRows, setDraftRows] = useState<PriorScheduleRow[] | null>(null);
  const [draftYear, setDraftYear] = useState(fiscalYear - 1);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await getPriorAssessmentSchedule(hoaId, fiscalYear);
      setStatus(next);
      if (next.seed?.rows?.length) {
        setDraftRows(next.seed.rows);
        setDraftYear(next.seed.fiscal_year);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load prior schedule status');
    } finally {
      setLoading(false);
    }
  }, [hoaId, fiscalYear]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleExtract = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    setError(null);
    try {
      const result = await extractPriorAssessmentSchedule(hoaId, files[0]);
      setDraftRows(result.rows.length ? result.rows : [{ recipient_label: '', monthly: '' }]);
      setDraftYear(fiscalYear - 1);
      if (!result.rows.length) {
        setError(result.message);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Extract failed');
    } finally {
      setBusy(false);
    }
  };

  const handleConfirm = async () => {
    if (!draftRows?.length) return;
    const cleaned = draftRows
      .map((r) => ({
        recipient_label: (r.recipient_label || '').trim(),
        monthly: String(r.monthly ?? '').trim(),
        ...(r.percent_of_total
          ? { percent_of_total: String(r.percent_of_total).trim() }
          : {}),
      }))
      .filter((r) => r.recipient_label && r.monthly);
    if (!cleaned.length) {
      setError('Add at least one unit with a monthly amount before confirming.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await confirmPriorAssessmentSchedule(hoaId, {
        fiscal_year: draftYear,
        rows: cleaned,
      });
      setDraftRows(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setBusy(false);
    }
  };

  const handleClear = async () => {
    setBusy(true);
    setError(null);
    try {
      await deletePriorAssessmentSchedule(hoaId);
      setDraftRows(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Clear failed');
    } finally {
      setBusy(false);
    }
  };

  const statusLabel =
    status?.status === 'inherited'
      ? 'Ready (from last finalized package)'
      : status?.status === 'seeded'
        ? 'Ready (operator seed)'
        : 'Missing — upload last year’s final package';

  return (
    <div className="space-y-3 rounded border border-[#e5e5e5] bg-white p-4">
      <div>
        <h3 className="text-sm font-semibold text-[#111111]">
          Prior-year assessment schedule
        </h3>
        <p className="mt-1 text-xs text-[#737373]">
          The final PDF shows last year’s unit assessments next to this year’s (
          {fiscalYear - 1} then {fiscalYear}). After you finalize a year here, the next
          year loads automatically. For year one, upload last year’s final disclosure PDF
          and confirm the extracted table.
        </p>
      </div>

      {loading ? (
        <p className="text-xs text-[#737373]">Checking prior schedule…</p>
      ) : (
        <p
          className={`rounded px-3 py-2 text-xs ${
            status?.status === 'missing'
              ? 'border border-[#fde68a] bg-[#fffbeb] text-[#92400e]'
              : 'border border-[#bbf7d0] bg-[#f0fdf4] text-[#166534]'
          }`}
        >
          {statusLabel}
          {status?.message ? ` — ${status.message}` : ''}
        </p>
      )}

      {status?.status !== 'inherited' ? (
        <FileDropzone
          title="Upload prior-year final package PDF"
          helper="Last year’s full board disclosure package (assessment schedule pages)."
          accept="application/pdf,.pdf"
          multiple={false}
          fileName={null}
          disabled={disabled || busy}
          status={busy ? 'attention' : 'idle'}
          statusMessage={busy ? 'Working…' : undefined}
          actionLabel="Choose PDF"
          onFilesSelected={(files) => void handleExtract(files)}
        />
      ) : null}

      {draftRows ? (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-xs text-[#525252]">
            <span>Seed year</span>
            <input
              type="number"
              className="w-20 rounded border border-[#d4d4d4] px-2 py-1"
              value={draftYear}
              disabled={disabled || busy}
              onChange={(e) => setDraftYear(Number(e.target.value) || fiscalYear - 1)}
            />
          </div>
          <div className="max-h-48 overflow-auto rounded border border-[#e5e5e5]">
            <table className="w-full text-xs">
              <thead className="bg-[#f5f5f5]">
                <tr>
                  <th className="px-2 py-1 text-left">Unit</th>
                  <th className="px-2 py-1 text-left">% (opt)</th>
                  <th className="px-2 py-1 text-left">Monthly</th>
                </tr>
              </thead>
              <tbody>
                {draftRows.map((row, idx) => (
                  <tr key={idx} className="border-t border-[#eee]">
                    <td className="px-1 py-0.5">
                      <input
                        className="w-full rounded border border-[#e5e5e5] px-1 py-0.5"
                        value={row.recipient_label}
                        disabled={disabled || busy}
                        onChange={(e) => {
                          const next = [...draftRows];
                          next[idx] = { ...row, recipient_label: e.target.value };
                          setDraftRows(next);
                        }}
                      />
                    </td>
                    <td className="px-1 py-0.5">
                      <input
                        className="w-full rounded border border-[#e5e5e5] px-1 py-0.5"
                        value={row.percent_of_total ?? ''}
                        disabled={disabled || busy}
                        onChange={(e) => {
                          const next = [...draftRows];
                          next[idx] = { ...row, percent_of_total: e.target.value };
                          setDraftRows(next);
                        }}
                      />
                    </td>
                    <td className="px-1 py-0.5">
                      <input
                        className="w-full rounded border border-[#e5e5e5] px-1 py-0.5"
                        value={row.monthly}
                        disabled={disabled || busy}
                        onChange={(e) => {
                          const next = [...draftRows];
                          next[idx] = { ...row, monthly: e.target.value };
                          setDraftRows(next);
                        }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              disabled={disabled || busy}
              onClick={() => void handleConfirm()}
            >
              Confirm prior schedule
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={disabled || busy}
              onClick={() =>
                setDraftRows((rows) => [
                  ...(rows ?? []),
                  { recipient_label: '', monthly: '' },
                ])
              }
            >
              Add row
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={disabled || busy}
              onClick={() => setDraftRows(null)}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      {status?.status === 'seeded' ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={disabled || busy}
          onClick={() => void handleClear()}
        >
          Clear seed
        </Button>
      ) : null}

      {error ? (
        <p className="rounded border border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-xs text-[#b91c1c]">
          {error}
        </p>
      ) : null}
    </div>
  );
}
