import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { Button } from './ui/button';
import {
  compareDraftToBaseline,
  getDraftCompareOptions,
  type BudgetDraftCompareOptionsResponse,
  type BudgetDraftCompareResponse,
  type BudgetDraftPayload,
} from '../api/budgetHistory';
import { formatCurrency, formatTimestamp } from '../lib/budget';
import { getErrorMessage } from '../lib/errors';

interface DraftBaselineComparePanelProps {
  hoaId: string;
  draftId: number;
  onPersistDraftSnapshot: () => Promise<BudgetDraftPayload>;
  compareDisabled?: boolean;
  isPersistingDraft?: boolean;
  isGenerating?: boolean;
}

function formatPercentDelta(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—';
  }
  return `${value.toFixed(1)}%`;
}

function optionLabel(option: BudgetDraftCompareOptionsResponse['baseline_versions'][number]) {
  const parts = [`${option.version_code} • ${option.stage}`];
  if (option.label) {
    parts.push(option.label);
  }
  if (option.is_recommended) {
    parts.push('recommended');
  }
  return parts.join(' • ');
}

function recommendationCopy(reason?: string | null) {
  if (reason === 'Latest Final') {
    return 'Recommended: Latest Final';
  }
  if (reason === 'Latest generated (no Final version yet)') {
    return 'Recommended: Latest generated (no Final version yet)';
  }
  return null;
}

export function DraftBaselineComparePanel({
  hoaId,
  draftId,
  onPersistDraftSnapshot,
  compareDisabled = false,
  isPersistingDraft = false,
  isGenerating = false,
}: DraftBaselineComparePanelProps) {
  const [compareOptions, setCompareOptions] = useState<BudgetDraftCompareOptionsResponse | null>(null);
  const [compareResult, setCompareResult] = useState<BudgetDraftCompareResponse | null>(null);
  const [selectedBaselineId, setSelectedBaselineId] = useState('');
  const [changedOnly, setChangedOnly] = useState(true);
  const [isLoadingOptions, setIsLoadingOptions] = useState(true);
  const [isComparing, setIsComparing] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadCompareOptions() {
      setIsLoadingOptions(true);
      setSelectedBaselineId('');
      setCompareResult(null);

      try {
        const response = await getDraftCompareOptions(hoaId);
        if (!cancelled) {
          setCompareOptions(response);
        }
      } catch (error) {
        if (!cancelled) {
          setCompareOptions(null);
          toast.error(getErrorMessage(error, 'Failed to load draft compare options.'));
        }
      } finally {
        if (!cancelled) {
          setIsLoadingOptions(false);
        }
      }
    }

    void loadCompareOptions();

    return () => {
      cancelled = true;
    };
  }, [hoaId, draftId]);

  const recommendedOption = useMemo(
    () => compareOptions?.baseline_versions.find((option) => option.is_recommended) ?? null,
    [compareOptions],
  );
  const recommendationText =
    recommendedOption && compareOptions?.recommended_baseline_reason
      ? recommendationCopy(compareOptions.recommended_baseline_reason)
      : null;
  const isActionDisabled =
    compareDisabled ||
    !selectedBaselineId ||
    isLoadingOptions ||
    isPersistingDraft ||
    isGenerating ||
    isComparing;
  const hasBaselines = Boolean(compareOptions?.baseline_versions.length);

  const handleCompare = async () => {
    if (!selectedBaselineId) {
      toast.error('Choose a baseline version first.');
      return;
    }

    setIsComparing(true);
    try {
      const persistedDraft = await onPersistDraftSnapshot();
      const response = await compareDraftToBaseline(hoaId, persistedDraft.id, {
        baseline_version_id: Number(selectedBaselineId),
        changed_only: changedOnly,
      });
      setCompareResult(response);
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to compare the draft to the selected baseline.'));
    } finally {
      setIsComparing(false);
    }
  };

  return (
    <section className="overflow-hidden rounded-2xl border border-[#e5e5e5] bg-white shadow-sm">
      <div className="border-b border-[#e5e5e5] bg-[#fafafa] px-6 py-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-[#111111]">Draft Baseline Compare</h2>
            <p className="mt-1 text-sm text-[#666666]">
              Compare the active draft against a historical generated version without changing Sync History compare behavior.
            </p>
          </div>
          {recommendationText ? (
            <div className="inline-flex items-center rounded-full border border-[#d4d4d4] bg-white px-3 py-1 text-xs font-medium text-[#111111]">
              {recommendationText}
            </div>
          ) : null}
        </div>
      </div>

      <div className="space-y-6 px-6 py-6">
        <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_auto_auto]">
          <div>
            <label className="mb-2 block text-sm font-medium text-[#111111]">Baseline version</label>
            <select
              value={selectedBaselineId}
              onChange={(event) => setSelectedBaselineId(event.target.value)}
              disabled={isLoadingOptions || !hasBaselines}
              className="w-full rounded-lg border border-[#d4d4d4] bg-white px-3 py-2 text-sm text-[#111111] shadow-sm focus:border-[#737373] focus:outline-none focus:ring-1 focus:ring-[#737373] disabled:cursor-not-allowed disabled:bg-[#f5f5f5]"
            >
              <option value="">Select baseline version</option>
              {compareOptions?.baseline_versions.map((option) => (
                <option key={option.id} value={option.id}>
                  {optionLabel(option)}
                </option>
              ))}
            </select>
            {recommendedOption ? (
              <p className="mt-2 text-xs text-[#666666]">
                Suggested baseline: {recommendedOption.version_code} •{' '}
                {formatTimestamp(new Date(recommendedOption.created_at))}
              </p>
            ) : null}
            {!isLoadingOptions && !hasBaselines ? (
              <p className="mt-2 text-xs text-[#666666]">
                No historical baselines are available yet. Generate a version first.
              </p>
            ) : null}
          </div>

          <label className="flex items-end gap-3 rounded-lg border border-[#e5e5e5] bg-[#fafafa] px-4 py-3 text-sm text-[#111111]">
            <input
              type="checkbox"
              checked={changedOnly}
              onChange={(event) => setChangedOnly(event.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-[#d4d4d4]"
            />
            Changed rows only
          </label>

          <div className="flex items-end justify-end">
            <Button
              onClick={() => void handleCompare()}
              disabled={isActionDisabled}
              className="w-full bg-[#111111] text-white hover:bg-[#262626] xl:w-auto"
            >
              {isComparing ? 'Comparing...' : 'Compare to Baseline'}
            </Button>
          </div>
        </div>

        {compareResult ? (
          <div className="rounded-xl border border-[#e5e5e5] bg-white p-5">
              <div className="mb-4">
                <p className="text-xs font-medium uppercase tracking-wide text-[#a3a3a3]">
                  Draft Changes vs Baseline
                </p>
                <h3 className="mt-1 text-lg font-semibold text-[#111111]">Editable draft changes only</h3>
                <p className="mt-2 text-sm text-[#666666]">
                  This section compares only operating and income rows from the active draft. Reserve rows stay excluded from editable budget logic.
                </p>
              </div>

              <div className="grid gap-4 lg:grid-cols-3">
                <div className="rounded-xl border border-[#e5e5e5] bg-[#fafafa] p-5">
                  <p className="text-xs font-medium uppercase tracking-wide text-[#a3a3a3]">Baseline</p>
                  <p className="mt-3 text-lg font-semibold text-[#111111]">
                    {formatCurrency(compareResult.draft_compare_summary.baseline_amount)}
                  </p>
                </div>
                <div className="rounded-xl border border-[#e5e5e5] bg-[#fafafa] p-5">
                  <p className="text-xs font-medium uppercase tracking-wide text-[#a3a3a3]">Current Draft</p>
                  <p className="mt-3 text-lg font-semibold text-[#111111]">
                    {formatCurrency(compareResult.draft_compare_summary.current_amount)}
                  </p>
                </div>
                <div className="rounded-xl border border-[#e5e5e5] bg-[#fafafa] p-5">
                  <p className="text-xs font-medium uppercase tracking-wide text-[#a3a3a3]">Amount Difference</p>
                  <p className="mt-3 text-lg font-semibold text-[#111111]">
                    {formatCurrency(compareResult.draft_compare_summary.delta_amount)} (
                    {formatPercentDelta(compareResult.draft_compare_summary.delta_percent)})
                  </p>
                </div>
              </div>

              <div className="mt-6 overflow-x-auto rounded-xl border border-[#e5e5e5]">
                <table className="w-full min-w-[900px]">
                  <thead className="border-b border-[#e5e5e5] bg-[#fafafa]">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-medium text-[#666666]">Line Item</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-[#666666]">Baseline</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-[#666666]">Current Draft</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-[#666666]">Amount Difference</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-[#666666]">% Difference vs Baseline</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-[#666666]">Note / Reason</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#e5e5e5] bg-white">
                    {compareResult.draft_compare_rows.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="px-4 py-5 text-sm text-[#666666]">
                          No editable draft rows match the current filter.
                        </td>
                      </tr>
                    ) : (
                      compareResult.draft_compare_rows.map((row) => (
                        <tr key={row.line_item_key}>
                          <td className="px-4 py-4 align-top text-sm text-[#111111]">
                            <div className="font-medium">{row.label}</div>
                          </td>
                          <td className="px-4 py-4 align-top text-sm text-[#111111]">
                            {formatCurrency(row.baseline_amount)}
                          </td>
                          <td className="px-4 py-4 align-top text-sm text-[#111111]">
                            {formatCurrency(row.current_amount)}
                          </td>
                          <td className="px-4 py-4 align-top text-sm text-[#111111]">
                            {formatCurrency(row.delta_amount)}
                          </td>
                          <td className="px-4 py-4 align-top text-sm text-[#111111]">
                            {formatPercentDelta(row.delta_percent)}
                          </td>
                          <td className="px-4 py-4 align-top text-sm text-[#111111]">
                            {row.note_title ? <div className="font-medium">{row.note_title}</div> : null}
                            <div className="mt-1 text-sm text-[#666666]">{row.note_body || '—'}</div>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
