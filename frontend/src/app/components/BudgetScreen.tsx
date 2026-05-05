import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { AlertTriangle, ArrowLeft, Download, FileText, Settings, Trash2, Upload } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from './ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from './ui/alert-dialog';
import { AISuggestionMode } from './AISuggestionMode';
import { BudgetView } from './BudgetView';
import { DraftBaselineComparePanel } from './DraftBaselineComparePanel';
import { EnrichedView } from './EnrichedView';
import {
  deleteActiveDraft,
  downloadBudgetDraftEnriched,
  mapBudgetHistoryLineItems,
  mapEditorLineItemsToBudgetHistory,
  saveBudgetDraft,
  uploadBudgetSource,
  type BudgetDraftPayload,
  type ExtractionQualityWarning,
} from '../api/budgetHistory';
import { type AISuggestion, type AISuggestionResponse, type FeedbackDecision, type LineItem } from '../data/mockData';
import type { HOARecord } from '../api/hoa';
import { formatTimestamp } from '../lib/budget';
import { getErrorMessage } from '../lib/errors';
import { computeTimingInputs } from '../lib/fiscalYear';
import { formatFiscalYearLabel } from '../lib/hoa';

interface GenerateBudgetRequest {
  draftId: number;
  lineItems: LineItem[];
  globalNote: string;
  statementMonth: number | null;
  growthFactor: number | null;
  growthFactorNote: string;
}

interface BudgetScreenProps {
  hoa: HOARecord;
  hoaId: string;
  lineItems: LineItem[];
  onLineItemsUpdate: (lineItems: LineItem[]) => void;
  onGenerateBudget: (payload: GenerateBudgetRequest) => Promise<void>;
  onDraftChange?: (draft: BudgetDraftPayload) => void;
  onDraftDeleted?: () => void;
  budgetGenerated: boolean;
  isGenerating?: boolean;
  initialView?: 'enriched' | 'budget' | 'ai';
  savedAiResponse?: AISuggestionResponse | null;
  onAiResponseChange?: (response: AISuggestionResponse | null) => void;
  activeDraft?: BudgetDraftPayload | null;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function normalizedDraftSnapshot(
  lineItems: LineItem[],
  globalNote: string,
  statementMonth: number | null,
  growthFactor: number | null,
  growthFactorNote: string,
) {
  return JSON.stringify({
    line_items: mapEditorLineItemsToBudgetHistory(lineItems),
    global_note: globalNote || null,
    statement_month: statementMonth,
    growth_factor: growthFactor,
    growth_factor_note: growthFactorNote || null,
  });
}

export function BudgetScreen({
  hoa,
  hoaId,
  lineItems,
  onLineItemsUpdate,
  onGenerateBudget,
  onDraftChange,
  onDraftDeleted,
  budgetGenerated,
  isGenerating = false,
  initialView = 'enriched',
  savedAiResponse = null,
  onAiResponseChange,
  activeDraft = null,
}: BudgetScreenProps) {
  const [uploadState, setUploadState] = useState<'initial' | 'uploading' | 'complete'>(
    activeDraft ? 'complete' : 'initial',
  );
  const [currentView, setCurrentView] = useState<'enriched' | 'budget' | 'ai'>(initialView);
  const [draftId, setDraftId] = useState<number | null>(activeDraft?.id ?? null);
  const [globalNote, setGlobalNote] = useState(activeDraft?.global_note ?? '');
  const [lastSaved, setLastSaved] = useState<Date | null>(
    activeDraft?.updated_at ? new Date(activeDraft.updated_at) : null,
  );
  const [statementMonth, setStatementMonth] = useState<number | null>(activeDraft?.statement_month ?? null);
  const [workingGrowthFactor, setWorkingGrowthFactor] = useState<number | null>(
    activeDraft?.growth_factor ?? null,
  );
  const [growthFactorNote, setGrowthFactorNote] = useState(activeDraft?.growth_factor_note ?? '');
  const [isComparePanelOpen, setIsComparePanelOpen] = useState(false);
  const [isSavingDraft, setIsSavingDraft] = useState(false);
  const [isDeletingDraft, setIsDeletingDraft] = useState(false);
  const [isDownloadingEnriched, setIsDownloadingEnriched] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [aiResponse, internalSetAiResponse] = useState<AISuggestionResponse | null>(savedAiResponse);
  const [appliedSuggestionSnapshot, setAppliedSuggestionSnapshot] = useState<
    Map<string, { feedbackCaseId: number; appliedPercent: number }> | null
  >(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  // One-shot dismissible quality warning shown when the backend used a
  // degraded extraction path (e.g. scanned-PDF vision-only fallback). State
  // lives only in this component, so reload clears it — by design.
  const [qualityWarning, setQualityWarning] = useState<ExtractionQualityWarning | null>(null);
  const activeReserveInflationRate =
    typeof hoa.reserve_inflation_rate === 'number' && Number.isFinite(hoa.reserve_inflation_rate)
      ? hoa.reserve_inflation_rate
      : 0;

  const fiscalYearLabel = formatFiscalYearLabel(
    hoa.fiscal_year_start_month,
    hoa.fiscal_year_end_month,
  );

  const setAiResponse = (response: AISuggestionResponse | null) => {
    internalSetAiResponse(response);
    onAiResponseChange?.(response);
    if (!response) {
      setAppliedSuggestionSnapshot(null);
    }
  };

  useEffect(() => {
    setCurrentView(initialView);
  }, [initialView]);

  useEffect(() => {
    setAiResponse(savedAiResponse);
  }, [savedAiResponse]);

  useEffect(() => {
    if (!activeDraft) {
      setDraftId(null);
      setGlobalNote('');
      setLastSaved(null);
      setStatementMonth(null);
      setWorkingGrowthFactor(null);
      setGrowthFactorNote('');
      setIsComparePanelOpen(false);
      setUploadState('initial');
      return;
    }

    setDraftId(activeDraft.id);
    setGlobalNote(activeDraft.global_note ?? '');
    setLastSaved(activeDraft.updated_at ? new Date(activeDraft.updated_at) : null);
    setStatementMonth(activeDraft.statement_month ?? null);
    setWorkingGrowthFactor(activeDraft.growth_factor ?? null);
    setGrowthFactorNote(activeDraft.growth_factor_note ?? '');
    setUploadState('complete');
  }, [activeDraft]);

  const handleSelectFile = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    event.target.value = '';

    setUploadState('uploading');
    try {
      const response = await uploadBudgetSource(hoaId, file);

      // Review-required path: the backend accepted the upload (HTTP 200) but
      // refused to build a draft because extraction failed or validation
      // flagged blocking issues. response.draft is null in this case.
      // Surface the review_reason + warnings and stay on the upload screen —
      // the user's next step is to upload a different file or report the issue.
      if (!response.draft) {
        const reason =
          response.review_reason ||
          'We could not build a draft from this file. Please verify the statement and try again.';
        toast.error(reason, { duration: 12000 });
        for (const w of response.warnings ?? []) {
          toast.warning(w, { duration: 10000 });
        }
        setUploadState('initial');
        return;
      }

      const mappedLineItems = mapBudgetHistoryLineItems(response.draft.line_items);
      onLineItemsUpdate(mappedLineItems);
      setDraftId(response.draft.id);
      setGlobalNote(response.draft.global_note ?? '');
      setStatementMonth(response.draft.statement_month ?? null);
      setWorkingGrowthFactor(response.draft.growth_factor ?? null);
      setGrowthFactorNote(response.draft.growth_factor_note ?? '');
      setLastSaved(response.draft.updated_at ? new Date(response.draft.updated_at) : null);
      onDraftChange?.(response.draft);
      setUploadState('complete');
      toast.success('Income statement uploaded and draft created.');
      // Show parser warnings (e.g. Tier 3 fallback)
      for (const w of response.warnings ?? []) {
        toast.warning(w, { duration: 10000 });
      }
      // If the backend took a degraded extraction path (e.g. scanned-PDF
      // vision-only fallback), pop a one-shot dismissible dialog so the
      // user knows to double-check the numbers before saving.
      if (response.extraction_quality_warning) {
        setQualityWarning(response.extraction_quality_warning);
      }
    } catch (error) {
      toast.error(getErrorMessage(error, 'Upload failed. Please try again.'));
      setUploadState('initial');
    }
  };

  const handlePercentChange = (itemId: string, newPercent: number) => {
    onLineItemsUpdate(
      lineItems.map((item) =>
        item.id === itemId ? { ...item, percentChange: newPercent } : item,
      ),
    );
  };

  const handleSaveDraft = async () => {
    try {
      await persistDraftSnapshot();
      toast.success('Draft saved.');
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to save draft.'));
    }
  };

  // Auto-save: debounce 2s after line items, notes, or growth factor change
  const autoSaveRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isMountedRef = useRef(false);

  useEffect(() => {
    // Skip the initial mount — only auto-save on subsequent changes
    if (!isMountedRef.current) {
      isMountedRef.current = true;
      return;
    }
    if (!draftId || isSavingDraft) return;

    if (autoSaveRef.current) clearTimeout(autoSaveRef.current);
    autoSaveRef.current = setTimeout(async () => {
      try {
        await persistDraftSnapshot();
        setLastSaved(new Date());
      } catch {
        // Silent — manual save is still available as fallback
      }
    }, 2000);

    return () => {
      if (autoSaveRef.current) clearTimeout(autoSaveRef.current);
    };
  }, [lineItems, globalNote, workingGrowthFactor, growthFactorNote]);

  const handleDiscardDraft = async () => {
    if (!draftId) return;
    if (!window.confirm('Discard this draft? This cannot be undone.')) return;

    setIsDeletingDraft(true);
    try {
      await deleteActiveDraft(hoaId);
      onDraftDeleted?.();
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to discard draft.'));
    } finally {
      setIsDeletingDraft(false);
    }
  };

  const persistDraftSnapshot = async (): Promise<BudgetDraftPayload> => {
    if (!draftId) {
      throw new Error('Upload an income statement first.');
    }

    setIsSavingDraft(true);
    try {
      const response = await saveBudgetDraft(hoaId, {
        draft_id: draftId,
        line_items: mapEditorLineItemsToBudgetHistory(lineItems),
        global_note: globalNote || null,
        statement_month: statementMonth,
        growth_factor: workingGrowthFactor,
        growth_factor_note: growthFactorNote || null,
      });
      onDraftChange?.(response.draft);
      setLastSaved(response.draft.updated_at ? new Date(response.draft.updated_at) : new Date());
      return response.draft;
    } catch (error) {
      throw error;
    } finally {
      setIsSavingDraft(false);
    }
  };

  const hasUnsavedDraftChanges = (() => {
    if (!activeDraft) {
      return Boolean(draftId);
    }

    const persistedEditorSnapshot = normalizedDraftSnapshot(
      mapBudgetHistoryLineItems(activeDraft.line_items),
      activeDraft.global_note ?? '',
      activeDraft.statement_month ?? null,
      activeDraft.growth_factor ?? null,
      activeDraft.growth_factor_note ?? '',
    );
    const workingSnapshot = normalizedDraftSnapshot(
      lineItems,
      globalNote,
      statementMonth,
      workingGrowthFactor,
      growthFactorNote,
    );
    return persistedEditorSnapshot !== workingSnapshot;
  })();

  const ensurePersistedDraftSnapshot = async (): Promise<BudgetDraftPayload> => {
    let persistedDraft = activeDraft;
    if (!persistedDraft || hasUnsavedDraftChanges) {
      persistedDraft = await persistDraftSnapshot();
    }
    if (!persistedDraft) {
      throw new Error('Draft unavailable.');
    }
    return persistedDraft;
  };

  const handleDownloadEnriched = async () => {
    if (!draftId) {
      toast.error('Upload an income statement first.');
      return;
    }

    setIsDownloadingEnriched(true);
    try {
      const persistedDraft = await ensurePersistedDraftSnapshot();
      const blob = await downloadBudgetDraftEnriched(hoaId, persistedDraft.id);
      downloadBlob(blob, `draft-${persistedDraft.id}-enriched.xlsx`);
      toast.success(`Draft ${persistedDraft.id} enriched workbook downloaded.`);
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to download the enriched draft.'));
    } finally {
      setIsDownloadingEnriched(false);
    }
  };

  const handleNoteSaved = async (itemId: string, title: string, body: string) => {
    const updatedItems = lineItems.map((entry) =>
      entry.id === itemId ? { ...entry, note: { title, body } } : entry,
    );
    onLineItemsUpdate(updatedItems);

    // Persist the note into the draft immediately so it survives page refresh
    if (draftId) {
      try {
        const response = await saveBudgetDraft(hoaId, {
          draft_id: draftId,
          line_items: mapEditorLineItemsToBudgetHistory(updatedItems),
          global_note: globalNote || null,
          statement_month: statementMonth,
          growth_factor: workingGrowthFactor,
          growth_factor_note: growthFactorNote || null,
        });
        onDraftChange?.(response.draft);
        setLastSaved(response.draft.updated_at ? new Date(response.draft.updated_at) : new Date());
      } catch {
        // Auto-save will catch it on the next cycle
      }
    }
  };

  const submitImplicitAiFeedback = async () => {
    if (!aiResponse?.run_id || !appliedSuggestionSnapshot || appliedSuggestionSnapshot.size === 0) {
      return;
    }

    const decisions: FeedbackDecision[] = [];
    for (const [lineItemId, { feedbackCaseId, appliedPercent }] of appliedSuggestionSnapshot) {
      const currentItem = lineItems.find((item) => item.id === lineItemId);
      if (!currentItem) continue;

      const currentPercent = currentItem.percentChange;
      const wasEdited = Math.abs(currentPercent - appliedPercent) > 0.001;

      decisions.push({
        feedbackCaseId,
        decision: wasEdited ? 'modified' : 'accepted',
        finalPctChange: currentPercent / 100,
        note: wasEdited
          ? `Edited after AI suggestion (AI: ${appliedPercent.toFixed(1)}%, final: ${currentPercent.toFixed(1)}%)`
          : undefined,
      });
    }

    if (decisions.length > 0) {
      try {
        const { submitAIFeedback } = await import('../api/macros');
        await submitAIFeedback({ runId: aiResponse.run_id, decisions });
      } catch {
        // Fire-and-forget: don't block generation
      }
      setAppliedSuggestionSnapshot(null);
    }
  };

  const handleGenerateBudgetClick = async () => {
    if (!draftId) {
      toast.error('Upload an income statement first.');
      return;
    }

    void submitImplicitAiFeedback();

    try {
      const persistedDraft = await ensurePersistedDraftSnapshot();
      await onGenerateBudget({
        draftId: persistedDraft.id,
        lineItems,
        globalNote,
        statementMonth,
        growthFactor: workingGrowthFactor,
        growthFactorNote,
      });
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to generate budget. Please try again.'));
    }
  };

  const handleFetchAISuggestions = async () => {
    if (aiLoading) return;
    setAiLoading(true);
    setAiError(null);
    try {
      const { getAISuggestions } = await import('../api/macros');
      const totalBudget = lineItems.reduce((sum, item) => sum + (item.annualBudget || 0), 0);
      const totalYtd = lineItems.reduce((sum, item) => sum + (item.ytdActual || 0), 0);
      const timing = computeTimingInputs(hoa, statementMonth ?? undefined);
      const result = await getAISuggestions({
        lineItems,
        propertyName: hoa.name || 'HOA',
        totalAnnualBudget: totalBudget,
        totalYtdActuals: totalYtd,
        ...timing,
        growthFactor: workingGrowthFactor ?? timing.growthFactor,
        fiscalYear: hoa.portfolio_year ?? new Date().getFullYear(),
        statementMonth: statementMonth ?? timing.statementMonth,
      });
      setAiResponse(result);
    } catch (error) {
      setAiError(getErrorMessage(error, 'AI service unavailable'));
    } finally {
      setAiLoading(false);
    }
  };

  const handleApplyAISuggestions = (selectedSuggestions: AISuggestion[]) => {
    const snapshot = new Map<string, { feedbackCaseId: number; appliedPercent: number }>();
    for (const s of selectedSuggestions) {
      if (s.feedbackCaseId != null) {
        snapshot.set(s.lineItemId, {
          feedbackCaseId: s.feedbackCaseId,
          appliedPercent: s.suggestedPercent,
        });
      }
    }
    setAppliedSuggestionSnapshot(snapshot);

    onLineItemsUpdate(
      lineItems.map((item) => {
        const suggestion = selectedSuggestions.find((entry) => entry.lineItemId === item.id);
        if (suggestion) {
          return { ...item, percentChange: suggestion.suggestedPercent };
        }
        return item;
      }),
    );
    setCurrentView('enriched');
    toast.success(`Applied ${selectedSuggestions.length} AI suggestions`);
  };

  if (uploadState === 'initial' || uploadState === 'uploading') {
    return (
      <div className="min-h-screen bg-[#fafafa]">
        <header className="sticky top-0 z-10 border-b border-[#e5e5e5] bg-white shadow-sm">
          <div className="flex items-center justify-between px-8 py-6">
            <div className="flex items-center gap-6">
              <Link to="/workspace" className="rounded-lg p-2 transition-colors hover:bg-[#f5f5f5]">
                <ArrowLeft className="h-5 w-5 text-[#525252]" />
              </Link>
              <div>
                <h1 className="text-xl font-semibold text-[#111111]">{hoa.name}</h1>
                <p className="text-sm text-[#737373]">Fiscal Year: {fiscalYearLabel}</p>
              </div>
            </div>
            <Link to={`/hoa/${hoaId}/settings`}>
              <Button variant="ghost" size="icon" className="hover:bg-[#f5f5f5]">
                <Settings className="h-5 w-5 text-[#525252]" />
              </Button>
            </Link>
          </div>
        </header>

        <main className="mx-auto max-w-3xl px-8 py-16">
          <div className="rounded-xl border-2 border-dashed border-[#d4d4d4] bg-white p-16 text-center shadow-sm">
            <div className="flex flex-col items-center gap-6">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-[#f5f5f5]">
                <Upload className="h-8 w-8 text-[#525252]" />
              </div>
              <div>
                <h2 className="mb-2 text-xl font-semibold text-[#111111]">Upload Income Statement</h2>
                <p className="text-sm text-[#737373]">
                  {uploadState === 'uploading'
                    ? 'Parsing document and creating a persisted draft...'
                    : 'Upload your Excel or PDF income statement to begin'}
                </p>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx,.xls,.pdf"
                className="hidden"
                onChange={handleFileChange}
              />
              <Button
                onClick={handleSelectFile}
                disabled={uploadState === 'uploading'}
                className="bg-[#111111] px-6 py-2.5 text-white shadow-sm hover:bg-[#262626]"
              >
                {uploadState === 'uploading' ? 'Processing...' : 'Select File'}
              </Button>
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#fafafa]">
      {qualityWarning ? (
        <AlertDialog
          open={true}
          onOpenChange={(open) => {
            if (!open) setQualityWarning(null);
          }}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle className="flex items-center gap-2 text-[#92400e]">
                <AlertTriangle className="h-5 w-5" aria-hidden="true" />
                {qualityWarning.title}
              </AlertDialogTitle>
              <AlertDialogDescription className="whitespace-pre-line text-[#525252]">
                {qualityWarning.body}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogAction onClick={() => setQualityWarning(null)}>
                Got it
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      ) : null}
      <header className="sticky top-0 z-10 border-b border-[#e5e5e5] bg-white shadow-sm">
        <div className="px-8 py-6">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-6">
              <Link to="/workspace" className="rounded-lg p-2 transition-colors hover:bg-[#f5f5f5]">
                <ArrowLeft className="h-5 w-5 text-[#525252]" />
              </Link>
              <div>
                <h1 className="text-xl font-semibold text-[#111111]">{hoa.name}</h1>
                <div className="mt-1 flex items-center gap-3">
                  <p className="text-sm text-[#737373]">Fiscal Year: {fiscalYearLabel}</p>
                  <span className="text-[#d4d4d4]">•</span>
                  <p className="text-xs text-[#737373]">Draft {draftId ?? 'Unavailable'}</p>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="text-right">
                <span className="text-xs text-[#a3a3a3]">Last Saved</span>
                <p className="text-sm font-medium text-[#525252]">
                  {lastSaved ? formatTimestamp(lastSaved) : 'Not saved yet'}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={handleSaveDraft}
                disabled={isSavingDraft || isGenerating || !draftId}
                className="border-[#d4d4d4] text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
              >
                {isSavingDraft ? 'Saving...' : 'Save Draft'}
              </Button>
              {draftId && !budgetGenerated && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void handleDiscardDraft()}
                  disabled={isDeletingDraft || isSavingDraft || isGenerating}
                  className="border-red-200 text-red-600 hover:border-red-300 hover:bg-red-50"
                >
                  <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                  {isDeletingDraft ? 'Discarding...' : 'Discard Draft'}
                </Button>
              )}
              <div className="h-8 w-px bg-[#e5e5e5]"></div>
              <Link to={`/hoa/${hoaId}/sync-history`}>
                <Button
                  variant="outline"
                  size="sm"
                  className="border-[#d4d4d4] px-4 font-medium text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
                >
                  View Past Sync
                </Button>
              </Link>
              <Link to={`/hoa/${hoaId}/settings`}>
                <Button variant="ghost" size="icon" className="hover:bg-[#f5f5f5]">
                  <Settings className="h-5 w-5 text-[#525252]" />
                </Button>
              </Link>
            </div>
          </div>

          <div className="border-t border-[#e5e5e5] pt-4">
            <details className="group">
              <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium text-[#111111] transition-colors hover:text-[#525252]">
                <FileText className="h-4 w-4" />
                Context Note
                <span className="ml-2 text-xs font-normal text-[#737373]">
                  (Strategic notes, board decisions, inflation assumptions)
                </span>
              </summary>
              <div className="mt-4 space-y-3">
                <textarea
                  value={globalNote}
                  onChange={(event) => setGlobalNote(event.target.value)}
                  placeholder="Add strategic fiscal year notes, board decisions, inflation assumptions..."
                  className="min-h-24 w-full resize-y rounded-lg border border-[#e5e5e5] bg-white p-4 text-sm text-[#111111] shadow-sm placeholder:text-[#a3a3a3] focus:border-[#737373] focus:ring-1 focus:ring-[#737373]"
                />
                {workingGrowthFactor != null && (
                  <p className="text-xs text-[#737373]">
                    Growth factor {workingGrowthFactor.toFixed(4)}
                    {growthFactorNote ? ` • ${growthFactorNote}` : ''}
                    {statementMonth ? ` • statement month ${statementMonth}` : ''}
                  </p>
                )}
              </div>
            </details>
          </div>
        </div>
      </header>

      <div className="sticky top-[140px] z-20 border-b border-[#e5e5e5] bg-white shadow-md">
        <div className="flex items-center justify-between bg-white/95 px-8 py-5 backdrop-blur-sm">
          <div className="flex items-center gap-2">
            <Button
              variant={currentView === 'enriched' ? 'default' : 'outline'}
              onClick={() => setCurrentView('enriched')}
              className={
                currentView === 'enriched'
                  ? 'bg-[#111111] text-white shadow-sm hover:bg-[#262626]'
                  : 'border-[#e5e5e5] text-[#525252] hover:border-[#737373] hover:bg-[#f5f5f5]'
              }
            >
              View Enriched
            </Button>
            <Button
              variant={currentView === 'budget' ? 'default' : 'outline'}
              onClick={() => setCurrentView('budget')}
              className={
                currentView === 'budget'
                  ? 'bg-[#111111] text-white shadow-sm hover:bg-[#262626]'
                  : 'border-[#e5e5e5] text-[#525252] hover:border-[#737373] hover:bg-[#f5f5f5]'
              }
            >
              View Budget
            </Button>
            <Button
              variant={currentView === 'ai' ? 'default' : 'outline'}
              onClick={() => setCurrentView('ai')}
              className={
                currentView === 'ai'
                  ? 'bg-[#111111] text-white shadow-sm hover:bg-[#262626]'
                  : 'border-[#e5e5e5] text-[#525252] hover:border-[#737373] hover:bg-[#f5f5f5]'
              }
            >
              AI Suggested % Change
            </Button>
            <Button
              variant="outline"
              className="border-[#e5e5e5] text-[#525252] hover:border-[#737373] hover:bg-[#f5f5f5]"
              onClick={() => void handleDownloadEnriched()}
              disabled={!draftId || isDownloadingEnriched || isSavingDraft || isGenerating}
            >
              <Download className="mr-2 h-4 w-4" />
              {isDownloadingEnriched ? 'Downloading...' : 'Download Enriched'}
            </Button>
            {draftId && currentView === 'enriched' && (
              <Button
                variant={isComparePanelOpen ? 'default' : 'outline'}
                onClick={() => setIsComparePanelOpen((open) => !open)}
                className={
                  isComparePanelOpen
                    ? 'bg-[#111111] text-white shadow-sm hover:bg-[#262626]'
                    : 'border-[#e5e5e5] text-[#525252] hover:border-[#737373] hover:bg-[#f5f5f5]'
                }
              >
                {isComparePanelOpen ? 'Hide Baseline Compare' : 'Compare to Baseline'}
              </Button>
            )}
          </div>
          {currentView === 'enriched' && (
            <Button
              onClick={handleGenerateBudgetClick}
              disabled={isGenerating || !draftId}
              className="bg-[#111111] text-white shadow-sm hover:bg-[#262626]"
            >
              {isGenerating ? 'Generating...' : budgetGenerated ? 'Regenerate Budget' : 'Generate Budget'}
            </Button>
          )}
        </div>
      </div>

      <main className="px-8 py-8">
        {currentView === 'enriched' && (
          <>
            {draftId && isComparePanelOpen ? (
              <div className="mb-8">
                <DraftBaselineComparePanel
                  hoaId={hoaId}
                  draftId={draftId}
                  onPersistDraftSnapshot={ensurePersistedDraftSnapshot}
                  isPersistingDraft={isSavingDraft}
                  isGenerating={isGenerating}
                />
              </div>
            ) : null}
            <EnrichedView
              hoaId={hoaId}
              draftId={draftId}
              lineItems={lineItems}
              onPercentChange={handlePercentChange}
              onNoteSaved={handleNoteSaved}
              units={hoa.units}
              reserveInflationRate={activeReserveInflationRate}
            />
          </>
        )}
        {currentView === 'budget' && (
          <BudgetView
            lineItems={lineItems}
            units={hoa.units}
            reserveInflationRate={activeReserveInflationRate}
          />
        )}
        {currentView === 'ai' && (
          <AISuggestionMode
            aiResponse={aiResponse}
            lineItems={lineItems}
            loading={aiLoading}
            error={aiError}
            onApply={handleApplyAISuggestions}
            onRefetch={handleFetchAISuggestions}
          />
        )}
      </main>
    </div>
  );
}
