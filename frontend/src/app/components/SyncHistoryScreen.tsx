import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import {
  ArrowLeft,
  ChevronRight,
  Download,
  Eye,
  History,
  RotateCcw,
  Settings,
} from 'lucide-react';
import { toast } from 'sonner';

import { Button } from './ui/button';
import {
  compareBudgetVersions,
  downloadBudgetDraftEnriched,
  downloadBudgetVersionFile,
  getBudgetHistory,
  reopenBudgetVersion,
  updateBudgetVersionMetadata,
  type BudgetDraftSummary,
  type BudgetHistoryResponse,
  type BudgetNoteRecord,
  type BudgetTimelineEvent,
  type BudgetVersionCompareCard,
  type BudgetVersionSummary,
} from '../api/budgetHistory';
import { getHOA, type HOARecord } from '../api/hoa';
import { formatCurrency, formatTimestamp } from '../lib/budget';
import { downloadBlob } from '../lib/downloadBlob';
import { formatReserveInflation } from '../lib/formatReserveInflation';
import { assessmentModeLabel } from '../lib/assessmentMode';
import { budgetSourceModeLabel } from '../lib/budgetSourceMode';
import { getErrorMessage } from '../lib/errors';
import { formatFiscalYearLabel } from '../lib/hoa';
import { resolveBudgetEntryCta } from '../lib/budgetEntryState';

function eventMetadata(event: BudgetTimelineEvent) {
  const payload = event.payload ?? {};
  const relatedFile =
    event.file_name ||
    (typeof payload.filename === 'string' ? payload.filename : undefined);
  const relatedVersion =
    event.version_code ||
    (typeof payload.version_code === 'string' ? payload.version_code : undefined) ||
    (typeof payload.source_version_code === 'string' ? payload.source_version_code : undefined);
  const sourceMode =
    typeof payload.source_mode === 'string'
      ? budgetSourceModeLabel(payload.source_mode as 'income_statement' | 'proforma_final_budget')
      : undefined;
  const assessmentMode =
    typeof payload.assessment_mode === 'string'
      ? assessmentModeLabel(payload.assessment_mode as 'fixed' | 'variable')
      : undefined;
  return { relatedFile, relatedVersion, sourceMode, assessmentMode };
}

function monthLabel(month: number | null | undefined) {
  if (!month) {
    return '—';
  }
  return new Date(2000, month - 1, 1).toLocaleString('en-US', { month: 'long' });
}

function compareFieldValue(
  version: BudgetVersionCompareCard,
  key:
    | 'version_code'
    | 'stage'
    | 'label'
    | 'created_at'
    | 'created_by_name'
    | 'source_upload_filename'
    | 'total_income'
    | 'total_expense'
    | 'net_operating_income'
    | 'growth_factor'
    | 'growth_factor_note'
    | 'statement_month'
    | 'fiscal_year_start_month'
    | 'fiscal_year_end_month'
    | 'source_mode'
    | 'assessment_mode',
) {
  if (key === 'created_at') {
    return formatTimestamp(new Date(version.created_at));
  }
  if (key === 'total_income' || key === 'total_expense' || key === 'net_operating_income') {
    return formatCurrency(version[key]);
  }
  if (key === 'growth_factor') {
    return version.growth_factor != null ? version.growth_factor.toFixed(4) : '—';
  }
  if (key === 'statement_month') {
    return monthLabel(version.statement_month);
  }
  if (key === 'fiscal_year_start_month' || key === 'fiscal_year_end_month') {
    return monthLabel(version[key]);
  }
  if (key === 'source_mode') {
    return budgetSourceModeLabel(version.source_mode ?? 'income_statement');
  }
  if (key === 'assessment_mode') {
    return assessmentModeLabel(version.assessment_mode ?? 'variable');
  }
  return version[key] || '—';
}

export function SyncHistoryScreen() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [hoa, setHoa] = useState<HOARecord | null>(null);
  const [history, setHistory] = useState<BudgetHistoryResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [screenError, setScreenError] = useState<string | null>(null);
  const [selectedNote, setSelectedNote] = useState<BudgetNoteRecord | null>(null);
  const [selectedVersions, setSelectedVersions] = useState<number[]>([]);
  const [compareCards, setCompareCards] = useState<BudgetVersionCompareCard[] | null>(null);
  const [isComparing, setIsComparing] = useState(false);
  const [reviewVersion, setReviewVersion] = useState<BudgetVersionSummary | null>(null);
  const [metadataForm, setMetadataForm] = useState({
    stage: 'Interim' as 'Interim' | 'Final',
    label: '',
    summary_note: '',
  });
  const [isSavingMetadata, setIsSavingMetadata] = useState(false);
  const [isDownloadingVersionId, setIsDownloadingVersionId] = useState<number | null>(null);
  const [isDownloadingDraftId, setIsDownloadingDraftId] = useState<number | null>(null);
  const [showReopenConfirm, setShowReopenConfirm] = useState(false);
  const [isReopening, setIsReopening] = useState(false);

  const loadHistoryScreen = async (hoaId: string) => {
    const [hoaResponse, historyResponse] = await Promise.all([
      getHOA(hoaId),
      getBudgetHistory(hoaId),
    ]);
    setHoa(hoaResponse);
    setHistory(historyResponse);
  };

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (!id) {
        setScreenError('HOA not found');
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      setScreenError(null);

      try {
        await loadHistoryScreen(id);
      } catch (error) {
        if (!cancelled) {
          setScreenError(getErrorMessage(error, 'Failed to load sync history.'));
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    if (!reviewVersion) {
      return;
    }
    setMetadataForm({
      stage: reviewVersion.stage === 'Final' ? 'Final' : 'Interim',
      label: reviewVersion.label ?? '',
      summary_note: reviewVersion.summary_note ?? '',
    });
  }, [reviewVersion]);

  const selectedSnapshots = useMemo(() => {
    if (!history) {
      return [];
    }
    return history.versions.filter((version) => selectedVersions.includes(version.id));
  }, [history, selectedVersions]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white">
        <p className="text-[#666666]">Loading sync history...</p>
      </div>
    );
  }

  if (!hoa || !history || !id || screenError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white">
        <p className="text-[#666666]">{screenError || 'HOA not found'}</p>
      </div>
    );
  }

  const fiscalYearLabel = formatFiscalYearLabel(
    hoa.fiscal_year_start_month,
    hoa.fiscal_year_end_month,
  );

  const handleCompare = async () => {
    if (selectedSnapshots.length !== 2) {
      toast.error('Please select exactly 2 versions to compare.');
      return;
    }

    setIsComparing(true);
    try {
      const response = await compareBudgetVersions(
        id,
        selectedSnapshots[0].id,
        selectedSnapshots[1].id,
      );
      setCompareCards(response.versions);
    } catch (error) {
      toast.error(getErrorMessage(error, 'Unable to compare the selected versions.'));
    } finally {
      setIsComparing(false);
    }
  };

  const toggleVersionSelection = (versionId: number) => {
    setSelectedVersions((previous) => {
      if (previous.includes(versionId)) {
        return previous.filter((entry) => entry !== versionId);
      }
      if (previous.length >= 2) {
        toast.error('You can only compare 2 versions at a time.');
        return previous;
      }
      return [...previous, versionId];
    });
  };

  const handleMetadataSave = async () => {
    if (!id || !reviewVersion) {
      return;
    }

    setIsSavingMetadata(true);
    try {
      const response = await updateBudgetVersionMetadata(id, reviewVersion.id, {
        stage: metadataForm.stage,
        label: metadataForm.label || null,
        summary_note: metadataForm.summary_note || null,
      });
      setReviewVersion(response.version);
      await loadHistoryScreen(id);
      toast.success('Version metadata saved.');
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to update version metadata.'));
    } finally {
      setIsSavingMetadata(false);
    }
  };

  const handleDownloadVersion = async (version: BudgetVersionSummary) => {
    if (!id) {
      return;
    }

    setIsDownloadingVersionId(version.id);
    try {
      const blob = await downloadBudgetVersionFile(id, version.id);
      downloadBlob(blob, `${version.version_code}-budget.xlsx`);
      await loadHistoryScreen(id);
      toast.success(`${version.version_code} downloaded.`);
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to download the version workbook.'));
    } finally {
      setIsDownloadingVersionId(null);
    }
  };

  const handleDownloadDraft = async (draft: BudgetDraftSummary) => {
    if (!id) {
      return;
    }

    setIsDownloadingDraftId(draft.id);
    try {
      const blob = await downloadBudgetDraftEnriched(id, draft.id);
      downloadBlob(blob, `draft-${draft.id}-enriched.xlsx`);
      await loadHistoryScreen(id);
      toast.success(`Draft ${draft.id} enriched workbook downloaded.`);
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to download the enriched draft.'));
    } finally {
      setIsDownloadingDraftId(null);
    }
  };

  const handleReopenVersion = async () => {
    if (!id || !reviewVersion) {
      return;
    }

    setIsReopening(true);
    try {
      const response = await reopenBudgetVersion(id, reviewVersion.id);
      navigate(`/hoa/${id}?draftId=${response.draft.id}&view=enriched`);
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to reopen this version as a draft.'));
      setIsReopening(false);
      setShowReopenConfirm(false);
    }
  };

  if (compareCards && compareCards.length === 2) {
    const compareFields: Array<{
      key:
        | 'version_code'
        | 'stage'
        | 'label'
        | 'created_at'
        | 'created_by_name'
        | 'source_upload_filename'
        | 'total_income'
        | 'total_expense'
        | 'net_operating_income'
        | 'growth_factor'
        | 'growth_factor_note'
        | 'statement_month'
        | 'fiscal_year_start_month'
        | 'fiscal_year_end_month'
        | 'source_mode'
        | 'assessment_mode';
      label: string;
    }> = [
      { key: 'version_code', label: 'Version Code' },
      { key: 'stage', label: 'Stage' },
      { key: 'label', label: 'Label' },
      { key: 'created_at', label: 'Created At' },
      { key: 'created_by_name', label: 'Created By' },
      { key: 'source_upload_filename', label: 'Source Upload' },
      { key: 'total_income', label: 'Total Income' },
      { key: 'total_expense', label: 'Total Expense' },
      { key: 'net_operating_income', label: 'Net Operating Income' },
      { key: 'growth_factor', label: 'Growth Factor' },
      { key: 'growth_factor_note', label: 'Growth Factor Note' },
      { key: 'statement_month', label: 'Statement Month' },
      { key: 'fiscal_year_start_month', label: 'Fiscal Year Start Month' },
      { key: 'fiscal_year_end_month', label: 'Fiscal Year End Month' },
      { key: 'source_mode', label: 'Source Mode' },
      { key: 'assessment_mode', label: 'Assessment Mode' },
    ];

    return (
      <div className="min-h-screen bg-white">
        <header className="sticky top-0 z-10 border-b border-[#e5e5e5] bg-white shadow-sm">
          <div className="px-8 py-6">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-6">
                <button
                  onClick={() => setCompareCards(null)}
                  className="rounded-lg p-2 transition-colors hover:bg-[#f5f5f5]"
                >
                  <ArrowLeft className="h-5 w-5 text-[#525252]" />
                </button>
                <div>
                  <h1 className="text-xl font-semibold text-[#111111]">Budget Version Comparison</h1>
                  <p className="text-sm text-[#737373]">
                    {hoa.name} • Compare requires exactly two immutable versions
                  </p>
                </div>
              </div>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-7xl px-8 py-12">
          <div className="grid gap-8 lg:grid-cols-2">
            {compareCards.map((version) => (
              <section key={version.id} className="overflow-hidden rounded-lg border border-[#e5e5e5] bg-white">
                <div className="border-b border-[#e5e5e5] bg-[#fafafa] px-6 py-5">
                  <h2 className="text-lg font-semibold text-[#111111]">{version.version_code}</h2>
                  <p className="mt-1 text-sm text-[#737373]">
                    {version.stage}
                    {version.label ? ` • ${version.label}` : ''}
                  </p>
                </div>
                <div className="divide-y divide-[#e5e5e5]">
                  {compareFields.map((field) => (
                    <div key={field.key} className="flex items-start justify-between gap-4 px-6 py-4">
                      <p className="text-xs font-medium uppercase tracking-wide text-[#a3a3a3]">
                        {field.label}
                      </p>
                      <p className="text-right text-sm text-[#111111]">
                        {compareFieldValue(version, field.key)}
                      </p>
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white">
      <header className="sticky top-0 z-10 border-b border-[#e5e5e5] bg-white shadow-sm">
        <div className="px-8 py-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-6">
              <Link
                to={`/hoa/${id}`}
                className="rounded-lg p-2 transition-colors hover:bg-[#f5f5f5]"
              >
                <ArrowLeft className="h-5 w-5 text-[#525252]" />
              </Link>
              <div>
                <h1 className="text-xl font-semibold text-[#111111]">HOA Sync History</h1>
                <p className="text-sm text-[#737373]">
                  {hoa.name} • Fiscal Year: {fiscalYearLabel}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="text-right">
                <p className="text-xs text-[#a3a3a3]">Latest Activity</p>
                <p className="text-sm font-medium text-[#525252]">
                  {history.timeline[0] ? formatTimestamp(new Date(history.timeline[0].occurred_at)) : 'No history yet'}
                </p>
              </div>
              <div className="h-8 w-px bg-[#e5e5e5]"></div>
              <Link to={`/hoa/${id}/settings`}>
                <Button variant="ghost" size="icon" className="hover:bg-[#f5f5f5]">
                  <Settings className="h-5 w-5 text-[#525252]" />
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </header>

      <main className="space-y-8 px-8 py-8">
        <div className="overflow-hidden rounded-lg border border-[#E5E5E5] bg-white">
          <div className="border-b border-[#E5E5E5] p-6">
            <h2 className="text-lg font-medium text-[#111111]">Major Events Timeline</h2>
          </div>
          <div className="divide-y divide-[#E5E5E5]">
            {history.timeline.length === 0 ? (
              <div className="p-6 text-sm text-[#666666]">No persisted events yet for this HOA.</div>
            ) : (
              history.timeline.map((event) => {
                const { relatedFile, relatedVersion, sourceMode, assessmentMode } = eventMetadata(event);
                return (
                  <div key={event.id} className="flex items-start justify-between gap-6 px-6 py-4">
                    <div className="flex items-start gap-3">
                      <ChevronRight className="mt-0.5 h-4 w-4 text-[#666666]" />
                      <div>
                        <p className="text-sm font-medium text-[#111111]">{event.summary}</p>
                        <p className="mt-1 text-xs text-[#666666]">{event.event_type}</p>
                        <p className="mt-2 text-sm text-[#666666]">
                          {event.actor_name} • {formatTimestamp(new Date(event.occurred_at))}
                        </p>
                        {(relatedFile || relatedVersion) && (
                          <p className="mt-1 text-xs text-[#666666]">
                            {relatedFile ? `File: ${relatedFile}` : null}
                            {relatedFile && relatedVersion ? ' • ' : null}
                            {relatedVersion ? `Version: ${relatedVersion}` : null}
                          </p>
                        )}
                        {sourceMode ? (
                          <p className="mt-1 text-xs text-[#666666]">Source mode: {sourceMode}</p>
                        ) : null}
                        {assessmentMode ? (
                          <p className="mt-1 text-xs text-[#666666]">Assessment mode: {assessmentMode}</p>
                        ) : null}
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-[#E5E5E5] bg-white">
          <div className="flex items-center justify-between border-b border-[#E5E5E5] p-6">
            <div>
              <h2 className="text-lg font-medium text-[#111111]">Draft Snapshots</h2>
              <p className="mt-1 text-sm text-[#666666]">
                Historical draft states can download their persisted enriched workbook. Only the active draft can reopen in the editor.
              </p>
            </div>
            {(() => {
              const latest = history.versions?.[0] ?? null;
              const entry = resolveBudgetEntryCta({
                hoaId: id ?? '',
                hasActiveDraft: Boolean(history.active_draft),
                latestVersionId: latest?.id ?? null,
                latestVersionCode: latest?.version_code ?? null,
              });
              return (
                <div className="flex flex-wrap items-center gap-2">
                  <Link to={entry.href}>
                    <Button variant="outline" className="border-[#d4d4d4] text-[#111111] hover:bg-[#f5f5f5]">
                      {entry.label}
                    </Button>
                  </Link>
                  {entry.secondaryHref && entry.secondaryLabel ? (
                    <Link to={entry.secondaryHref}>
                      <Button variant="ghost" className="text-[#525252]">
                        {entry.secondaryLabel}
                      </Button>
                    </Link>
                  ) : null}
                </div>
              );
            })()}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-[#E5E5E5] bg-[#F7F7F7]">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Draft</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Status</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Last Updated</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Source Upload</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Reopened From</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Actor</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#E5E5E5]">
                {history.drafts.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-6 py-4 text-sm text-[#666666]">
                      No persisted drafts yet.
                    </td>
                  </tr>
                ) : (
                  history.drafts.map((draft) => (
                    <tr key={draft.id} className="hover:bg-[#F7F7F7]">
                      <td className="px-6 py-4 text-sm font-medium text-[#111111]">Draft {draft.id}</td>
                      <td className="px-6 py-4 text-sm text-[#666666]">{draft.status}</td>
                      <td className="px-6 py-4 text-sm text-[#666666]">
                        {draft.updated_at ? formatTimestamp(new Date(draft.updated_at)) : '—'}
                      </td>
                      <td className="px-6 py-4 text-sm text-[#666666]">
                        <div>{draft.source_upload_filename || '—'}</div>
                        <div className="mt-1 text-xs text-[#999999]">
                          {budgetSourceModeLabel(draft.source_mode ?? 'income_statement')}
                        </div>
                        <div className="mt-1 text-xs text-[#999999]">
                          {assessmentModeLabel(draft.assessment_mode ?? 'variable')}
                        </div>
                      </td>
                      <td className="px-6 py-4 text-sm text-[#666666]">
                        {draft.reopened_from_version_code || '—'}
                      </td>
                      <td className="px-6 py-4 text-sm text-[#666666]">{draft.actor_name}</td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => void handleDownloadDraft(draft)}
                            disabled={isDownloadingDraftId === draft.id}
                          >
                            <Download className="mr-1 h-4 w-4" />
                            {isDownloadingDraftId === draft.id ? 'Downloading...' : 'Download Enriched'}
                          </Button>
                          {draft.status === 'active' ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => navigate(`/hoa/${id}?draftId=${draft.id}&view=enriched`)}
                            >
                              <Eye className="mr-1 h-4 w-4" />
                              Open Draft
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-[#E5E5E5] bg-white">
          <div className="flex items-center justify-between border-b border-[#E5E5E5] p-6">
            <div>
              <h2 className="text-lg font-medium text-[#111111]">Generated Budget Versions</h2>
              <p className="mt-1 text-sm text-[#666666]">
                Select two persisted versions to compare. Historical review stays read-only until you choose Reopen as Draft.
              </p>
            </div>
            <Button
              onClick={() => void handleCompare()}
              disabled={selectedSnapshots.length !== 2 || isComparing}
              className="bg-[#000000] text-white hover:bg-[#111111] disabled:bg-[#d4d4d4]"
            >
              {isComparing ? 'Comparing...' : 'Compare Selected'}
            </Button>
          </div>
          <div className="border-b border-[#E5E5E5] bg-[#fafafa] px-6 py-3 text-sm text-[#666666]">
            {selectedSnapshots.length === 2
              ? 'Two versions selected. Compare is ready.'
              : `Select exactly two versions to compare. Current selection: ${selectedVersions.length}`}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-[#E5E5E5] bg-[#F7F7F7]">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Select</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Version</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Generated On</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Actor</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Label</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Total Income</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Total Expense</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Net Operating Income</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#E5E5E5]">
                {history.versions.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="px-6 py-4 text-sm text-[#666666]">
                      No immutable versions have been generated yet.
                    </td>
                  </tr>
                ) : (
                  history.versions.map((version) => (
                    <tr key={version.id} className="hover:bg-[#F7F7F7]">
                      <td className="px-6 py-4">
                        <input
                          type="checkbox"
                          checked={selectedVersions.includes(version.id)}
                          onChange={() => toggleVersionSelection(version.id)}
                          className="h-4 w-4 rounded border-[#E5E5E5]"
                        />
                      </td>
                      <td className="px-6 py-4 text-sm text-[#111111]">
                        <div>{version.version_code} • {version.stage}</div>
                        <div className="mt-1 text-xs text-[#666666]">
                          Reserve inflation {formatReserveInflation(version.reserve_inflation_rate)}
                        </div>
                        <div className="mt-1 text-xs text-[#666666]">
                          {budgetSourceModeLabel(version.source_mode ?? 'income_statement')}
                        </div>
                        <div className="mt-1 text-xs text-[#666666]">
                          {assessmentModeLabel(version.assessment_mode ?? 'variable')}
                        </div>
                      </td>
                      <td className="px-6 py-4 text-sm text-[#666666]">
                        {formatTimestamp(new Date(version.created_at))}
                      </td>
                      <td className="px-6 py-4 text-sm text-[#666666]">{version.created_by_name}</td>
                      <td className="px-6 py-4 text-sm text-[#111111]">{version.label || '—'}</td>
                      <td className="px-6 py-4 text-sm text-[#111111]">
                        {formatCurrency(version.total_income)}
                      </td>
                      <td className="px-6 py-4 text-sm text-[#111111]">
                        {formatCurrency(version.total_expense)}
                      </td>
                      <td className="px-6 py-4 text-sm text-[#111111]">
                        {formatCurrency(version.net_operating_income)}
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2">
                          <Button variant="ghost" size="sm" onClick={() => setReviewVersion(version)}>
                            <Eye className="mr-1 h-4 w-4" />
                            View
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => void handleDownloadVersion(version)}
                            disabled={isDownloadingVersionId === version.id}
                          >
                            <Download className="mr-1 h-4 w-4" />
                            {isDownloadingVersionId === version.id ? 'Downloading...' : 'Download'}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-[#E5E5E5] bg-white">
          <div className="border-b border-[#E5E5E5] p-6">
            <h2 className="text-lg font-medium text-[#111111]">Saved Notes & Annotations</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-[#E5E5E5] bg-[#F7F7F7]">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Date</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Scope</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Line Item</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Title</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">Actor</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[#666666]">View</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#E5E5E5]">
                {history.notes.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-6 py-4 text-sm text-[#666666]">
                      No persisted notes yet.
                    </td>
                  </tr>
                ) : (
                  history.notes.map((note) => (
                    <tr key={note.id} className="hover:bg-[#F7F7F7]">
                      <td className="px-6 py-4 text-sm text-[#111111]">
                        {formatTimestamp(new Date(note.created_at))}
                      </td>
                      <td className="px-6 py-4">
                        <span className="inline-flex rounded border border-[#E5E5E5] bg-[#F7F7F7] px-2 py-1 text-xs">
                          {note.note_scope}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-sm text-[#666666]">{note.line_item_key || '—'}</td>
                      <td className="px-6 py-4 text-sm text-[#111111]">{note.title}</td>
                      <td className="px-6 py-4 text-sm text-[#666666]">{note.created_by_name}</td>
                      <td className="px-6 py-4">
                        <Button variant="ghost" size="sm" onClick={() => setSelectedNote(note)}>
                          <Eye className="mr-1 h-4 w-4" />
                          View
                        </Button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </main>

      {reviewVersion && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/30">
          <div className="h-full w-full max-w-xl overflow-y-auto bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-[#E5E5E5] p-6">
              <div>
                <h3 className="text-lg font-medium text-[#111111]">Historical Version Review</h3>
                <p className="mt-1 text-sm text-[#666666]">
                  Review metadata here. Line items stay read-only until you reopen this version as a draft.
                </p>
              </div>
              <button
                onClick={() => {
                  setReviewVersion(null);
                  setShowReopenConfirm(false);
                }}
                className="text-[#666666] hover:text-[#111111]"
              >
                ✕
              </button>
            </div>

            <div className="space-y-6 p-6">
              <div className="rounded-lg border border-[#E5E5E5] bg-[#fafafa] p-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-sm font-semibold text-[#111111]">
                      {reviewVersion.version_code} • {reviewVersion.stage}
                    </p>
                    <p className="mt-1 text-sm text-[#666666]">
                      {reviewVersion.created_by_name} • {formatTimestamp(new Date(reviewVersion.created_at))}
                    </p>
                    {reviewVersion.source_upload_filename ? (
                      <p className="mt-2 text-sm text-[#666666]">
                        Source upload: {reviewVersion.source_upload_filename}
                      </p>
                    ) : null}
                    <p className="mt-2 text-sm text-[#666666]">
                      Source mode: {budgetSourceModeLabel(reviewVersion.source_mode ?? 'income_statement')}
                    </p>
                    <p className="mt-2 text-sm text-[#666666]">
                      Assessment mode: {assessmentModeLabel(reviewVersion.assessment_mode ?? 'variable')}
                    </p>
                  </div>
                  <History className="h-5 w-5 text-[#525252]" />
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div className="rounded-lg border border-[#E5E5E5] p-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-[#a3a3a3]">Total Income</p>
                  <p className="mt-2 text-lg font-semibold text-[#111111]">
                    {formatCurrency(reviewVersion.total_income)}
                  </p>
                </div>
                <div className="rounded-lg border border-[#E5E5E5] p-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-[#a3a3a3]">Total Expense</p>
                  <p className="mt-2 text-lg font-semibold text-[#111111]">
                    {formatCurrency(reviewVersion.total_expense)}
                  </p>
                </div>
                <div className="rounded-lg border border-[#E5E5E5] p-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-[#a3a3a3]">
                    Reserve inflation
                  </p>
                  <p className="mt-2 text-lg font-semibold text-[#111111]">
                    {formatReserveInflation(reviewVersion.reserve_inflation_rate)}
                  </p>
                </div>
              </div>

              <div className="space-y-3">
                <p className="text-sm font-medium text-[#111111]">Stage</p>
                <div className="flex items-center gap-3">
                  <Button
                    type="button"
                    variant={metadataForm.stage === 'Interim' ? 'default' : 'outline'}
                    onClick={() => setMetadataForm((current) => ({ ...current, stage: 'Interim' }))}
                    className={metadataForm.stage === 'Interim' ? 'bg-[#111111] text-white' : ''}
                  >
                    Interim
                  </Button>
                  <Button
                    type="button"
                    variant={metadataForm.stage === 'Final' ? 'default' : 'outline'}
                    onClick={() => setMetadataForm((current) => ({ ...current, stage: 'Final' }))}
                    className={metadataForm.stage === 'Final' ? 'bg-[#111111] text-white' : ''}
                  >
                    Final
                  </Button>
                </div>
              </div>

              <div>
                <label className="mb-2 block text-sm font-medium text-[#111111]" htmlFor="version-label">
                  Human Label
                </label>
                <input
                  id="version-label"
                  value={metadataForm.label}
                  onChange={(event) =>
                    setMetadataForm((current) => ({ ...current, label: event.target.value }))
                  }
                  placeholder="Board Review Draft"
                  className="w-full rounded-lg border border-[#E5E5E5] px-3 py-2 text-sm text-[#111111] focus:border-[#111111] focus:outline-none"
                />
              </div>

              <div>
                <label className="mb-2 block text-sm font-medium text-[#111111]" htmlFor="version-summary-note">
                  Summary Note
                </label>
                <textarea
                  id="version-summary-note"
                  value={metadataForm.summary_note}
                  onChange={(event) =>
                    setMetadataForm((current) => ({ ...current, summary_note: event.target.value }))
                  }
                  placeholder="Audit-safe version note"
                  className="min-h-28 w-full rounded-lg border border-[#E5E5E5] px-3 py-2 text-sm text-[#111111] focus:border-[#111111] focus:outline-none"
                />
                <p className="mt-2 text-xs text-[#666666]">
                  This screen does not allow editable line-item mutation. Draft-level and line-item notes still save through the existing saveBudgetNote() workflow in the editor.
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <Button
                  onClick={() => void handleMetadataSave()}
                  disabled={isSavingMetadata}
                  className="bg-[#111111] text-white hover:bg-[#262626]"
                >
                  {isSavingMetadata ? 'Saving...' : 'Save Metadata'}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => void handleDownloadVersion(reviewVersion)}
                  disabled={isDownloadingVersionId === reviewVersion.id}
                >
                  <Download className="mr-2 h-4 w-4" />
                  {isDownloadingVersionId === reviewVersion.id ? 'Downloading...' : 'Download Version'}
                </Button>
                {reviewVersion.source_draft_id ? (
                  <Button
                    variant="outline"
                    onClick={() =>
                      void handleDownloadDraft({
                        id: reviewVersion.source_draft_id as number,
                        status: 'generated',
                        source_upload_id: null,
                        source_upload_filename: reviewVersion.source_upload_filename ?? null,
                        reopened_from_version_id: null,
                        reopened_from_version_code: null,
                        updated_at: reviewVersion.created_at,
                        actor_name: reviewVersion.created_by_name,
                        enriched_file_available: true,
                      })
                    }
                    disabled={isDownloadingDraftId === reviewVersion.source_draft_id}
                  >
                    <Download className="mr-2 h-4 w-4" />
                    {isDownloadingDraftId === reviewVersion.source_draft_id
                      ? 'Downloading...'
                      : 'Download Source Draft Enriched'}
                  </Button>
                ) : null}
                <Button
                  variant="outline"
                  onClick={() => navigate(`/hoa/${id}?generated=true&versionId=${reviewVersion.id}&readOnly=1`)}
                >
                  <Eye className="mr-2 h-4 w-4" />
                  Open Full Read-Only View
                </Button>
              </div>

              <div className="rounded-lg border border-[#E5E5E5] bg-[#fafafa] p-4">
                <p className="text-sm font-medium text-[#111111]">Reopen as Draft</p>
                <p className="mt-2 text-sm text-[#666666]">
                  Reopening keeps {reviewVersion.version_code} immutable and creates a new active draft that routes back through draftId.
                </p>
                {!showReopenConfirm ? (
                  <Button
                    variant="outline"
                    onClick={() => setShowReopenConfirm(true)}
                    className="mt-4"
                  >
                    <RotateCcw className="mr-2 h-4 w-4" />
                    Reopen as Draft
                  </Button>
                ) : (
                  <div className="mt-4 rounded-lg border border-[#E5E5E5] bg-white p-4">
                    <p className="text-sm text-[#111111]">
                      Confirm reopen? The current active draft will be superseded and the editor will open
                      the new draft copied from {reviewVersion.version_code}.
                    </p>
                    <div className="mt-4 flex items-center gap-3">
                      <Button
                        onClick={() => void handleReopenVersion()}
                        disabled={isReopening}
                        className="bg-[#111111] text-white hover:bg-[#262626]"
                      >
                        {isReopening ? 'Reopening...' : 'Confirm Reopen'}
                      </Button>
                      <Button variant="outline" onClick={() => setShowReopenConfirm(false)}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {selectedNote && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/30">
          <div className="h-full w-full max-w-md overflow-y-auto bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-[#E5E5E5] p-6">
              <h3 className="text-lg font-medium text-[#111111]">Note Details</h3>
              <button
                onClick={() => setSelectedNote(null)}
                className="text-[#666666] hover:text-[#111111]"
              >
                ✕
              </button>
            </div>
            <div className="space-y-4 p-6">
              <div>
                <p className="mb-1 text-xs text-[#666666]">Date</p>
                <p className="text-sm text-[#111111]">
                  {formatTimestamp(new Date(selectedNote.created_at))}
                </p>
              </div>
              <div>
                <p className="mb-1 text-xs text-[#666666]">Note Scope</p>
                <p className="text-sm text-[#111111]">{selectedNote.note_scope}</p>
              </div>
              {selectedNote.line_item_key && (
                <div>
                  <p className="mb-1 text-xs text-[#666666]">Line Item</p>
                  <p className="text-sm text-[#111111]">{selectedNote.line_item_key}</p>
                </div>
              )}
              <div>
                <p className="mb-1 text-xs text-[#666666]">Created By</p>
                <p className="text-sm text-[#111111]">{selectedNote.created_by_name}</p>
              </div>
              <div>
                <p className="mb-1 text-xs text-[#666666]">Title</p>
                <p className="text-sm text-[#111111]">{selectedNote.title}</p>
              </div>
              <div>
                <p className="mb-1 text-xs text-[#666666]">Note Content</p>
                <p className="text-sm text-[#111111]">{selectedNote.body}</p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
