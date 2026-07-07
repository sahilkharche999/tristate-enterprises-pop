import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { AlertTriangle, ArrowLeft, Download, FileText, PackageCheck, Settings, Trash2, Upload } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
import { Label } from './ui/label';
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
import { GLMergeSuggestions } from './GLMergeSuggestions';
import { ReserveStudyView } from './ReserveStudyView';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { exportEnrichedBudget } from '../lib/exportBudget';
import {
  applyReserveStudyToBudget,
  commitBudgetGlMerge,
  replaceReserveStudy,
  deleteActiveDraft,
  fetchBudgetGlMergeSuggestions,
  getActiveBudgetDraft,
  listBudgetGlMerges,
  mapBudgetHistoryLineItems,
  mapEditorLineItemsToBudgetHistory,
  reserveStudyFileUrl,
  saveBudgetDraft,
  saveReserveStudyRows,
  unmergeBudgetGlMergeApplication,
  uploadBudgetBundle,
  uploadBudgetSource,
  type BudgetGlMergeListItem,
  type BudgetGlMergeSuggestionPayload,
  type BudgetSourceMode,
  type BudgetBundleUploadResponse,
  type BudgetDraftPayload,
  type ExtractionDebugInfo,
  type ExtractionQualityWarning,
  type ReserveStudyRow,
} from '../api/budgetHistory';
import { DrePdfCompareView } from './DrePdfCompareView';
import { type AISuggestion, type AISuggestionResponse, type FeedbackDecision, type LineItem } from '../data/mockData';
import type { HOARecord } from '../api/hoa';
import { formatCurrency, formatTimestamp } from '../lib/budget';
import {
  buildBudgetGlIdentity,
  findMergeCandidates,
  mergeSuggestionKey,
  resolveMergeSuggestionItems,
} from '../lib/glMerge.ts';
import { FileDropzone } from './fileDropzone';
import {
  glMergeSuggestionStorageKey,
  readGlMergeSuggestionCache,
  writeGlMergeSuggestionCache,
} from '../lib/glMergeSuggestionCache.ts';
import {
  ASSESSMENT_MODE_OPTIONS,
  assessmentModeHelperCopy,
  assessmentModeLabel,
  assessmentModeWorkflowCopy,
  type AssessmentMode,
} from '../lib/assessmentMode';
import {
  BUDGET_SOURCE_MODE_OPTIONS,
  budgetSourceModeCreateSuccess,
  budgetSourceModeGenericDraftError,
  budgetSourceModeHelperCopy,
  budgetSourceModeLabel,
  budgetSourceModeMissingUploadError,
  budgetSourceModeUploadPlaceholder,
  budgetSourceModeUploadTitle,
} from '../lib/budgetSourceMode';
import { getErrorMessage } from '../lib/errors';
import { computeTimingInputs } from '../lib/fiscalYear';
import { formatFiscalYearRangeLabel } from '../lib/hoa';

interface GenerateBudgetRequest {
  draftId: number;
  lineItems: LineItem[];
  globalNote: string;
  statementMonth: number | null;
  growthFactor: number | null;
  growthFactorNote: string;
}

interface MergeDialogState {
  primaryId: string | null;
  secondaryId: string | null;
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

const DRAFT_AUTO_SAVE_INTERVAL_MS = 30_000;

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

function normalizedReserveStudySnapshot(
  rows: ReserveStudyRow[],
  warnings: string[],
) {
  return JSON.stringify({
    rows,
    warnings,
  });
}

function reserveSnapshotFromDraft(draft: BudgetDraftPayload | null): string {
  return normalizedReserveStudySnapshot(
    ((draft?.reserve_study_rows ?? []) as ReserveStudyRow[]),
    draft?.reserve_study_warnings ?? [],
  );
}

function looksLikeFinalBudgetFilename(filename: string): boolean {
  return /\b(budget|final|pro[\s_-]*forma)\b/i.test(filename);
}

function renderExtractionDebug(debugInfo?: ExtractionDebugInfo | null) {
  if (!debugInfo) {
    return null;
  }

  return (
    <details className="mt-3 rounded-lg border border-[#e5e5e5] bg-[#fafafa] p-3 text-xs text-[#525252]">
      <summary className="cursor-pointer font-semibold text-[#111111]">
        Technical extraction details
      </summary>
      <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-white p-3 font-mono text-[11px] leading-5 text-[#262626]">
        {JSON.stringify(debugInfo, null, 2)}
      </pre>
    </details>
  );
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
  const [currentView, setCurrentView] = useState<'enriched' | 'budget' | 'ai' | 'reserve'>(initialView);
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
  const [isDownloadingEnriched] = useState(false);
  const [autoSaveEnabled, setAutoSaveEnabled] = useState(false);
  const [budgetSourceFile, setBudgetSourceFile] = useState<File | null>(null);
  const [reserveStudyFile, setReserveStudyFile] = useState<File | null>(null);
  const [budgetSourceMode, setBudgetSourceMode] = useState<BudgetSourceMode>(
    activeDraft?.source_mode ?? 'income_statement',
  );
  const [assessmentMode, setAssessmentMode] = useState<AssessmentMode>(
    activeDraft?.assessment_mode ?? hoa.assessment_mode ?? 'variable',
  );
  const [bundleUploadResult, setBundleUploadResult] = useState<BudgetBundleUploadResponse | null>(null);
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
  const [reserveStudyRows, setReserveStudyRows] = useState<ReserveStudyRow[]>([]);
  const [reserveStudyWarnings, setReserveStudyWarnings] = useState<string[]>([]);
  const [reserveStudyStatus, setReserveStudyStatus] = useState<string>('none');
  const [reserveStudyApplyMessage, setReserveStudyApplyMessage] = useState<string | null>(null);
  const [isSavingReserveStudy, setIsSavingReserveStudy] = useState(false);
  const [isApplyingReserveStudy, setIsApplyingReserveStudy] = useState(false);
  const reserveSavePromiseRef = useRef<Promise<BudgetDraftPayload> | null>(null);

  // "Compare with PDF" full-screen split view for the reserve study table
  // (add-reserve-study-pdf-compare-view). Tracked as local state (not read live
  // off the `activeDraft` prop) because it must stay current after a reserve
  // study replace/generate flow, the same reason `draftId` above is local state
  // rather than derived from `activeDraft` on every render.
  const [reserveStudyUploadId, setReserveStudyUploadId] = useState<number | null>(
    activeDraft?.reserve_study_upload_id ?? null,
  );
  const [isReserveCompareOpen, setIsReserveCompareOpen] = useState(false);
  const [reserveTargetPage, setReserveTargetPage] = useState<number | undefined>(undefined);
  const jumpToReservePage = useCallback((page: number) => {
    setReserveTargetPage(page);
    setIsReserveCompareOpen(true);
  }, []);
  const reserveRowsRef = useRef<ReserveStudyRow[]>([]);
  const reserveWarningsRef = useRef<string[]>([]);
  const autoSaveInFlightRef = useRef(false);
  const lastAutoSaveAttemptSnapshotRef = useRef<string | null>(null);
  const lastPersistedReserveSnapshotRef = useRef<string>(reserveSnapshotFromDraft(activeDraft));
  const allowReserveHydrationRef = useRef(true);
  const glMergeSuggestionCacheKeyRef = useRef<string | null>(null);
  const skipNextGlMergeSuggestionPersistRef = useRef(false);
  const [glMerges, setGlMerges] = useState<BudgetGlMergeListItem[]>([]);
  const [glMergeSuggestions, setGlMergeSuggestions] = useState<BudgetGlMergeSuggestionPayload[]>([]);
  const [glMergeSuggestionsLoading, setGlMergeSuggestionsLoading] = useState(false);
  const [glMergeSuggestionsError, setGlMergeSuggestionsError] = useState<string | null>(null);
  const [glMergeActionLoading, setGlMergeActionLoading] = useState(false);
  const [glMergeDialog, setGlMergeDialog] = useState<MergeDialogState>({
    primaryId: null,
    secondaryId: null,
  });
  const [unmergingApplicationId, setUnmergingApplicationId] = useState<number | null>(null);
  const [dismissedMergeSuggestionKeys, setDismissedMergeSuggestionKeys] = useState<string[]>([]);
  const activeReserveInflationRate =
    typeof hoa.reserve_inflation_rate === 'number' && Number.isFinite(hoa.reserve_inflation_rate)
      ? hoa.reserve_inflation_rate
      : typeof activeDraft?.reserve_inflation_rate === 'number' && Number.isFinite(activeDraft.reserve_inflation_rate)
        ? activeDraft.reserve_inflation_rate
        : 0;

  const fiscalYearLabel = formatFiscalYearRangeLabel(
    hoa.fiscal_year_start_month,
    hoa.fiscal_year_end_month,
    hoa.portfolio_year,
  );
  const visibleGlMergeSuggestions = glMergeSuggestions.filter(
    (suggestion) => !dismissedMergeSuggestionKeys.includes(mergeSuggestionKey(suggestion)),
  );
  const selectedMergePrimary =
    glMergeDialog.primaryId == null
      ? null
      : lineItems.find((item) => item.id === glMergeDialog.primaryId) ?? null;
  const mergeCandidates = selectedMergePrimary
    ? findMergeCandidates(lineItems, selectedMergePrimary.id)
    : [];
  const selectedMergeSecondary =
    glMergeDialog.secondaryId == null
      ? null
      : mergeCandidates.find((item) => item.id === glMergeDialog.secondaryId) ?? null;
  const selectedMergePreview = selectedMergePrimary && selectedMergeSecondary
    ? {
        ytdActual: selectedMergePrimary.ytdActual + selectedMergeSecondary.ytdActual,
        annualBudget: selectedMergePrimary.annualBudget + selectedMergeSecondary.annualBudget,
        projection:
          (selectedMergePrimary.projection ?? 0) + (selectedMergeSecondary.projection ?? 0),
        proposed:
          selectedMergePrimary.annualBudget * (1 + selectedMergePrimary.percentChange / 100) +
          selectedMergeSecondary.annualBudget * (1 + selectedMergeSecondary.percentChange / 100),
      }
    : null;

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
      setAssessmentMode(hoa.assessment_mode ?? 'variable');
    }
  }, [activeDraft, hoa.assessment_mode]);

  useEffect(() => {
    reserveRowsRef.current = reserveStudyRows;
    reserveWarningsRef.current = reserveStudyWarnings;
  }, [reserveStudyRows, reserveStudyWarnings]);

  const hydrateReserveState = (draft: BudgetDraftPayload) => {
    setReserveStudyRows((draft.reserve_study_rows ?? []) as ReserveStudyRow[]);
    setReserveStudyWarnings(draft.reserve_study_warnings ?? []);
    setReserveStudyStatus(draft.reserve_study_status ?? 'none');
    setReserveStudyUploadId(draft.reserve_study_upload_id ?? null);
    lastPersistedReserveSnapshotRef.current = reserveSnapshotFromDraft(draft);
  };

  useEffect(() => {
    if (!activeDraft) {
      setDraftId(null);
      setGlobalNote('');
      setLastSaved(null);
      setStatementMonth(null);
      setWorkingGrowthFactor(null);
      setGrowthFactorNote('');
      setIsComparePanelOpen(false);
      setReserveStudyRows([]);
      setReserveStudyWarnings([]);
      setReserveStudyStatus('none');
      lastPersistedReserveSnapshotRef.current = reserveSnapshotFromDraft(null);
      allowReserveHydrationRef.current = true;
      setBudgetSourceMode('income_statement');
      setAssessmentMode(hoa.assessment_mode ?? 'variable');
      setUploadState('initial');
      return;
    }

    const nextDraftId = activeDraft.id;
    const isDraftSwitch = draftId == null || nextDraftId !== draftId;
    const currentReserveSnapshot = normalizedReserveStudySnapshot(
      reserveRowsRef.current,
      reserveWarningsRef.current,
    );
    const hasUnsavedLocalReserveEdits =
      currentReserveSnapshot !== lastPersistedReserveSnapshotRef.current;

    setDraftId(activeDraft.id);
    setGlobalNote(activeDraft.global_note ?? '');
    setLastSaved(activeDraft.updated_at ? new Date(activeDraft.updated_at) : null);
    setStatementMonth(activeDraft.statement_month ?? null);
    setWorkingGrowthFactor(activeDraft.growth_factor ?? null);
    setGrowthFactorNote(activeDraft.growth_factor_note ?? '');
    setBudgetSourceMode(activeDraft.source_mode ?? 'income_statement');
    setAssessmentMode(activeDraft.assessment_mode ?? hoa.assessment_mode ?? 'variable');
    if (isDraftSwitch || allowReserveHydrationRef.current || !hasUnsavedLocalReserveEdits) {
      hydrateReserveState(activeDraft);
    }
    allowReserveHydrationRef.current = false;
    setUploadState('complete');
  }, [activeDraft, draftId, hoa.assessment_mode]);

  const hydrateDraftState = (draft: BudgetDraftPayload, options?: { hydrateReserve?: boolean }) => {
    const mappedLineItems = mapBudgetHistoryLineItems(draft.line_items);
    onLineItemsUpdate(mappedLineItems);
    setDraftId(draft.id);
    setGlobalNote(draft.global_note ?? '');
    setStatementMonth(draft.statement_month ?? null);
    setWorkingGrowthFactor(draft.growth_factor ?? null);
    setGrowthFactorNote(draft.growth_factor_note ?? '');
    setBudgetSourceMode(draft.source_mode ?? 'income_statement');
    setAssessmentMode(draft.assessment_mode ?? hoa.assessment_mode ?? 'variable');
    if (options?.hydrateReserve !== false) {
      hydrateReserveState(draft);
    }
    setLastSaved(draft.updated_at ? new Date(draft.updated_at) : null);
    allowReserveHydrationRef.current = options?.hydrateReserve !== false;
    onDraftChange?.(draft);
    setUploadState('complete');
  };

  const closeMergeDialog = () => {
    setGlMergeDialog({ primaryId: null, secondaryId: null });
  };

  const refreshDraftAndMerges = async () => {
    const refreshedDraft = await getActiveBudgetDraft(hoaId);
    hydrateDraftState(refreshedDraft, { hydrateReserve: false });
    const rows = await listBudgetGlMerges(hoaId);
    setGlMerges(rows);
    return refreshedDraft;
  };

  const handleMergeConflictRefresh = async (fallbackMessage: string) => {
    try {
      await refreshDraftAndMerges();
      toast.warning(fallbackMessage);
    } catch {
      toast.error(fallbackMessage);
    }
  };

  useEffect(() => {
    if (!draftId) {
      glMergeSuggestionCacheKeyRef.current = null;
      skipNextGlMergeSuggestionPersistRef.current = false;
      setGlMerges([]);
      setGlMergeSuggestions([]);
      setDismissedMergeSuggestionKeys([]);
      closeMergeDialog();
      return;
    }

    const cacheKey = glMergeSuggestionStorageKey(hoaId, draftId);
    glMergeSuggestionCacheKeyRef.current = cacheKey;
    skipNextGlMergeSuggestionPersistRef.current = true;

    if (typeof window !== 'undefined') {
      const cache = readGlMergeSuggestionCache(window.sessionStorage, hoaId, draftId);
      setGlMergeSuggestions(cache.suggestions);
      setDismissedMergeSuggestionKeys(cache.dismissedKeys);
    }

    void (async () => {
      try {
        const rows = await listBudgetGlMerges(hoaId);
        setGlMerges(rows);
      } catch {
        setGlMerges([]);
      }
    })();
  }, [draftId, hoaId]);

  useEffect(() => {
    if (!draftId || typeof window === 'undefined') {
      return;
    }

    const cacheKey = glMergeSuggestionStorageKey(hoaId, draftId);
    if (glMergeSuggestionCacheKeyRef.current !== cacheKey) {
      return;
    }
    if (skipNextGlMergeSuggestionPersistRef.current) {
      skipNextGlMergeSuggestionPersistRef.current = false;
      return;
    }

    writeGlMergeSuggestionCache(window.sessionStorage, hoaId, draftId, {
      suggestions: glMergeSuggestions,
      dismissedKeys: dismissedMergeSuggestionKeys,
    });
  }, [draftId, hoaId, glMergeSuggestions, dismissedMergeSuggestionKeys]);

  const handleBundleUpload = async () => {
    if (!budgetSourceFile || !reserveStudyFile) {
      toast.error('Select both the budget file and the reserve study PDF first.');
      return;
    }
    if (budgetSourceMode === 'income_statement' && looksLikeFinalBudgetFilename(budgetSourceFile.name)) {
      toast.warning(
        'This file name looks like a final/pro forma budget. If extraction fails, switch Source Mode to Pro Forma / Final Budget and retry.',
        { duration: 12000 },
      );
    }
    setUploadState('uploading');
    try {
      const response = await uploadBudgetBundle(
        hoaId,
        budgetSourceFile,
        reserveStudyFile,
        budgetSourceMode,
        assessmentMode,
      );
      setBundleUploadResult(response);

      if (!response.draft) {
        setUploadState('initial');
        toast.error(response.budget_source.review_reason || 'We could not create a draft from the selected files.');
        if (response.can_continue_with_reserve_study_only) {
          toast.warning('Reserve study upload was preserved. Replace the budget file to continue.');
        }
        return;
      }

      if (response.can_continue_with_budget_only) {
        setUploadState('initial');
        toast.warning(
          response.reserve_study.review_reason ||
            'Budget draft is ready, but the reserve study needs attention before it can be attached.',
          { duration: 10000 },
        );
        return;
      }

      hydrateDraftState(response.draft);
      toast.success('Budget draft created from both uploaded files.');
    } catch (error) {
      toast.error(getErrorMessage(error, 'Upload failed. Please try again.'));
      setUploadState('initial');
    }
  };

  const handleContinueWithBudgetOnly = () => {
    if (!bundleUploadResult?.draft) {
      return;
    }
    hydrateDraftState(bundleUploadResult.draft);
    toast.success('Continuing with the successful budget draft.');
  };

  const handleBudgetOnlyUpload = async () => {
    if (!budgetSourceFile) {
      toast.error('Select a budget file first.');
      return;
    }
    if (budgetSourceMode === 'income_statement' && looksLikeFinalBudgetFilename(budgetSourceFile.name)) {
      toast.warning(
        'This file name looks like a final/pro forma budget. If extraction fails, switch Source Mode to Pro Forma / Final Budget and retry.',
        { duration: 12000 },
      );
    }
    setUploadState('uploading');
    try {
      const response = await uploadBudgetSource(
        hoaId,
        budgetSourceFile,
        budgetSourceMode,
        assessmentMode,
      );
      if (!response.draft) {
        const reason =
          response.review_reason ||
          'We could not build a draft from this file. Please verify the statement and try again.';
        toast.error(reason, { duration: 12000 });
        for (const warning of response.warnings ?? []) {
          toast.warning(warning, { duration: 10000 });
        }
        if (response.debug_info?.code) {
          toast.warning(`Extraction detail: ${response.debug_info.code}`, { duration: 12000 });
        }
        setUploadState('initial');
        return;
      }

      hydrateDraftState(response.draft);
      toast.success(budgetSourceModeCreateSuccess(budgetSourceMode));
      for (const warning of response.warnings ?? []) {
        toast.warning(warning, { duration: 10000 });
      }
      if (response.extraction_quality_warning) {
        setQualityWarning(response.extraction_quality_warning);
      }
    } catch (error) {
      toast.error(getErrorMessage(error, 'Upload failed. Please try again.'));
      setUploadState('initial');
    }
  };

  const handleCreateDraftUpload = () => {
    if (reserveStudyFile) {
      void handleBundleUpload();
      return;
    }
    void handleBudgetOnlyUpload();
  };

  const handlePercentChange = (itemId: string, newPercent: number) => {
    onLineItemsUpdate(
      lineItems.map((item) =>
        item.id === itemId ? { ...item, percentChange: newPercent } : item,
      ),
    );
  };

  const handleLineItemFieldChange = (
    itemId: string,
    field: 'name' | 'ytdActual' | 'annualBudget' | 'projection',
    value: string,
  ) => {
    onLineItemsUpdate(
      lineItems.map((item) => {
        if (item.id !== itemId) return item;
        if (field === 'name') {
          return { ...item, name: value, label: value };
        }
        const numValue = parseFloat(value) || 0;
        return { ...item, [field]: numValue };
      }),
    );
  };

  const handleReserveStudyReplaced = (updatedDraft: BudgetDraftPayload) => {
    hydrateReserveState(updatedDraft);
    setCurrentView('reserve');
  };

  const handleReplaceReserveFile = async (file: File) => {
    if (!draftId) return;
    const updatedDraft = await replaceReserveStudy(hoaId, draftId, file);
    handleReserveStudyReplaced(updatedDraft);
  };

  const handleReadOnlyOverride = (itemId: string, override: boolean | null) => {
    onLineItemsUpdate(
      lineItems.map((item) => {
        if (item.id !== itemId) return item;
        // When unlocking (override=false): set readOnly=false so the row renders as editable.
        // When re-locking (override=null): restore readOnly from the category default.
        const isReserveCategory =
          item.category === 'reserve_income' || item.category === 'reserve_expense';
        const newReadOnly = override === null ? isReserveCategory : override;
        return { ...item, readOnlyOverride: override, readOnly: newReadOnly };
      }),
    );
  };

  const handleSaveDraft = async () => {
    try {
      await persistDraftSnapshot();
      if (hasUnsavedReserveStudyChanges) {
        await persistReserveStudySnapshot(reserveRowsRef.current, reserveWarningsRef.current);
      }
      toast.success('Draft saved.');
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to save draft.'));
    }
  };

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
      throw new Error(budgetSourceModeMissingUploadError(budgetSourceMode));
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

  const persistedDraftSnapshot = activeDraft
    ? normalizedDraftSnapshot(
        mapBudgetHistoryLineItems(activeDraft.line_items),
        activeDraft.global_note ?? '',
        activeDraft.statement_month ?? null,
        activeDraft.growth_factor ?? null,
        activeDraft.growth_factor_note ?? '',
      )
    : null;
  const workingDraftSnapshot = normalizedDraftSnapshot(
    lineItems,
    globalNote,
    statementMonth,
    workingGrowthFactor,
    growthFactorNote,
  );
  const hasUnsavedDraftChanges = activeDraft
    ? persistedDraftSnapshot !== workingDraftSnapshot
    : Boolean(draftId);

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

  const workingReserveStudySnapshot = normalizedReserveStudySnapshot(reserveStudyRows, reserveStudyWarnings);
  const hasUnsavedReserveStudyChanges =
    workingReserveStudySnapshot !== lastPersistedReserveSnapshotRef.current;

  const persistReserveStudySnapshot = async (
    rows: ReserveStudyRow[] = reserveRowsRef.current,
    warnings: string[] = reserveWarningsRef.current,
  ): Promise<BudgetDraftPayload> => {
    if (!draftId) {
      throw new Error('Create a draft first.');
    }

    const snapshotSent = normalizedReserveStudySnapshot(rows, warnings);
    const savePromise = (async () => {
      setIsSavingReserveStudy(true);
      try {
        const draft = await saveReserveStudyRows(hoaId, draftId, {
          rows: rows as unknown as Record<string, unknown>[],
          warnings,
        });
        const savedSnapshot = reserveSnapshotFromDraft(draft);
        lastPersistedReserveSnapshotRef.current = savedSnapshot;
        const localSnapshotNow = normalizedReserveStudySnapshot(
          reserveRowsRef.current,
          reserveWarningsRef.current,
        );
        if (localSnapshotNow === snapshotSent) {
          hydrateReserveState(draft);
        }
        setLastSaved(draft.updated_at ? new Date(draft.updated_at) : new Date());
        return draft;
      } finally {
        setIsSavingReserveStudy(false);
        if (reserveSavePromiseRef.current === savePromise) {
          reserveSavePromiseRef.current = null;
        }
      }
    })();
    reserveSavePromiseRef.current = savePromise;
    return savePromise;
  };

  const ensurePersistedReserveStudySnapshot = async (
    persistedDraft: BudgetDraftPayload | null,
  ): Promise<BudgetDraftPayload> => {
    let nextDraft = persistedDraft;
    if (reserveSavePromiseRef.current) {
      nextDraft = await reserveSavePromiseRef.current;
    }
    const latestReserveSnapshot = normalizedReserveStudySnapshot(
      reserveRowsRef.current,
      reserveWarningsRef.current,
    );
    if (!nextDraft || latestReserveSnapshot !== lastPersistedReserveSnapshotRef.current) {
      nextDraft = await persistReserveStudySnapshot(
        reserveRowsRef.current,
        reserveWarningsRef.current,
      );
    }
    return nextDraft;
  };

  useEffect(() => {
    if (!autoSaveEnabled || !draftId || isSavingDraft || isSavingReserveStudy) {
      return;
    }
    if (!hasUnsavedDraftChanges && !hasUnsavedReserveStudyChanges) {
      return;
    }

    const autoSaveSnapshot = JSON.stringify({
      draft: workingDraftSnapshot,
      reserve: workingReserveStudySnapshot,
    });
    if (lastAutoSaveAttemptSnapshotRef.current === autoSaveSnapshot) {
      return;
    }

    const timer = window.setTimeout(() => {
      if (autoSaveInFlightRef.current) {
        return;
      }
      autoSaveInFlightRef.current = true;
      lastAutoSaveAttemptSnapshotRef.current = autoSaveSnapshot;

      void (async () => {
        try {
          if (hasUnsavedDraftChanges) {
            await persistDraftSnapshot();
          }
          if (hasUnsavedReserveStudyChanges) {
            await persistReserveStudySnapshot(reserveRowsRef.current, reserveWarningsRef.current);
          }
        } catch (error) {
          lastAutoSaveAttemptSnapshotRef.current = null;
          toast.error(getErrorMessage(error, 'Auto-save failed.'));
        } finally {
          autoSaveInFlightRef.current = false;
        }
      })();
    }, DRAFT_AUTO_SAVE_INTERVAL_MS);

    return () => window.clearTimeout(timer);
  }, [
    autoSaveEnabled,
    draftId,
    isSavingDraft,
    isSavingReserveStudy,
    hasUnsavedDraftChanges,
    hasUnsavedReserveStudyChanges,
    workingDraftSnapshot,
    workingReserveStudySnapshot,
  ]);

  const handleDownloadEnriched = () => {
    if (!lineItems.length) {
      toast.error('No budget lines to export.');
      return;
    }
    try {
      const fiscalYear = hoa.portfolio_year ?? new Date().getFullYear();
      exportEnrichedBudget(lineItems, hoa.name, activeReserveInflationRate, fiscalYear);
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to generate the proforma export.'));
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
        // The note editor already confirmed locally; the draft save button can retry persistence.
      }
    }
  };

  const handleRequestMerge = (itemId: string) => {
    if (findMergeCandidates(lineItems, itemId).length === 0) {
      toast.error('No same-section candidates found for this row.');
      return;
    }
    setGlMergeDialog({ primaryId: itemId, secondaryId: null });
  };

  const handleDismissMergeSuggestion = (suggestion: BudgetGlMergeSuggestionPayload) => {
    setDismissedMergeSuggestionKeys((current) => [
      ...current,
      mergeSuggestionKey(suggestion),
    ]);
  };

  const handleFetchGlMergeSuggestions = async () => {
    if (!draftId) {
      toast.error('Create a draft before requesting merge suggestions.');
      return;
    }

    setGlMergeSuggestionsLoading(true);
    setGlMergeSuggestionsError(null);
    try {
      const suggestions = await fetchBudgetGlMergeSuggestions(hoaId);
      setGlMergeSuggestions(suggestions);
      setDismissedMergeSuggestionKeys([]);
      if (suggestions.length === 0) {
        toast.success('No merge suggestions found for this draft.');
      }
    } catch (error) {
      const message = getErrorMessage(error, 'Failed to load merge suggestions.');
      setGlMergeSuggestionsError(message);
      toast.error(message);
    } finally {
      setGlMergeSuggestionsLoading(false);
    }
  };

  const runBudgetGlMerge = async (
    primary: LineItem,
    secondary: LineItem,
    source: 'manual' | 'gemini_suggestion',
    suggestionToRemoveKey?: string,
  ) => {
    setGlMergeActionLoading(true);
    try {
      const persistedDraft = await ensurePersistedDraftSnapshot();
      await commitBudgetGlMerge(
        hoaId,
        {
          primary: buildBudgetGlIdentity(primary),
          secondary: buildBudgetGlIdentity(secondary),
          source,
        },
        persistedDraft.version_int,
      );
      await refreshDraftAndMerges();
      closeMergeDialog();
      if (suggestionToRemoveKey) {
        setGlMergeSuggestions((current) =>
          current.filter((entry) => mergeSuggestionKey(entry) !== suggestionToRemoveKey),
        );
      }
      toast.success(`Merged ${primary.name} with ${secondary.name}.`);
    } catch (error) {
      if (
        error &&
        typeof error === 'object' &&
        'status' in error &&
        (error.status === 412 || error.status === 428)
      ) {
        await handleMergeConflictRefresh('Draft changed while merging. Refreshed latest draft.');
        return;
      }
      toast.error(getErrorMessage(error, 'Failed to merge these budget rows.'));
    } finally {
      setGlMergeActionLoading(false);
    }
  };

  const handleApplyMergeSuggestion = async (suggestion: BudgetGlMergeSuggestionPayload) => {
    const resolved = resolveMergeSuggestionItems(lineItems, suggestion);
    if (!resolved) {
      setGlMergeSuggestionsError(
        'Suggestion no longer matches the active draft exactly. Use “Modify” or refresh suggestions.',
      );
      toast.error('Suggestion no longer matches the active draft exactly.');
      return;
    }
    await runBudgetGlMerge(
      resolved.primary,
      resolved.secondary,
      'gemini_suggestion',
      mergeSuggestionKey(suggestion),
    );
  };

  const handleModifyMergeSuggestion = (suggestion: BudgetGlMergeSuggestionPayload) => {
    const resolved = resolveMergeSuggestionItems(lineItems, suggestion);
    if (!resolved) {
      toast.error('Suggestion no longer maps cleanly. Choose a row manually instead.');
      return;
    }
    setGlMergeDialog({
      primaryId: resolved.primary.id,
      secondaryId: resolved.secondary.id,
    });
  };

  const handleCommitSelectedMerge = async () => {
    if (!selectedMergePrimary || !selectedMergeSecondary) {
      toast.error('Choose both rows before committing a merge.');
      return;
    }
    await runBudgetGlMerge(selectedMergePrimary, selectedMergeSecondary, 'manual');
  };

  const handleUnmergeMerge = async (merge: BudgetGlMergeListItem) => {
    if (!merge.application_id) {
      toast.error('Applied merge record not found.');
      return;
    }

    setUnmergingApplicationId(merge.application_id);
    try {
      const persistedDraft = await ensurePersistedDraftSnapshot();
      await unmergeBudgetGlMergeApplication(
        hoaId,
        merge.application_id,
        persistedDraft.version_int,
      );
      await refreshDraftAndMerges();
      toast.success(`Separated ${merge.primary_label} and ${merge.secondary_label}.`);
    } catch (error) {
      if (
        error &&
        typeof error === 'object' &&
        'status' in error &&
        (error.status === 412 || error.status === 428)
      ) {
        await handleMergeConflictRefresh('Draft changed while unmerging. Refreshed latest draft.');
        return;
      }
      toast.error(getErrorMessage(error, 'Failed to unmerge this application.'));
    } finally {
      setUnmergingApplicationId(null);
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
      toast.error(budgetSourceModeGenericDraftError());
      return;
    }

    void submitImplicitAiFeedback();

    try {
      const persistedDraft = await ensurePersistedDraftSnapshot();
      const persistedReserveDraft = await ensurePersistedReserveStudySnapshot(persistedDraft);
      await onGenerateBudget({
        draftId: persistedReserveDraft.id,
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

  const handleReserveStudyRowChange = (index: number, field: keyof ReserveStudyRow, value: string) => {
    setReserveStudyRows((current) =>
      current.map((row, rowIndex) => {
        if (rowIndex !== index) return row;
        if (field === 'line_item') {
          return { ...row, line_item: value };
        }
        if (row.row_type === 'header') {
          return row;
        }
        if (field === 'quantity') {
          return { ...row, quantity: value.trim() === '' ? null : value };
        }
        const nextValue = value === '' ? null : Number(value);
        const updates: Partial<ReserveStudyRow> = {
          [field]: Number.isFinite(nextValue as number) ? nextValue : null,
        };
        if (field === 'useful_life' || field === 'replacement_cost') {
          updates.year_replacement_provision = null;
          updates.estimated_liability = null;
        } else if (field === 'remaining_life' || field === 'year_new' || field === 'reference_year') {
          updates.estimated_liability = null;
        }
        return {
          ...row,
          ...updates,
        };
      }),
    );
    setReserveStudyApplyMessage(null);
  };

  const handleAddReserveStudyRow = () => {
    setReserveStudyRows((current) => [
      ...current,
      {
        row_id: `manual-${Date.now()}-${current.length}`,
        row_type: 'item',
        line_item: '',
        useful_life: null,
        remaining_life: null,
        quantity: null,
        replacement_cost: null,
        year_new: null,
        reference_year: null,
        year_replacement_provision: null,
        estimated_liability: null,
        source_page: null,
        flags: [],
      },
    ]);
    setReserveStudyApplyMessage(null);
  };

  const handleAddReserveStudyHeader = () => {
    setReserveStudyRows((current) => [
      ...current,
      {
        row_id: `manual-header-${Date.now()}-${current.length}`,
        row_type: 'header',
        line_item: 'New Header',
        useful_life: null,
        remaining_life: null,
        quantity: null,
        replacement_cost: null,
        year_new: null,
        reference_year: null,
        year_replacement_provision: null,
        estimated_liability: null,
        source_page: null,
        flags: [],
      },
    ]);
    setReserveStudyApplyMessage(null);
  };

  const handleMoveReserveStudyRow = (index: number, direction: 'up' | 'down') => {
    setReserveStudyRows((current) => {
      const targetIndex = direction === 'up' ? index - 1 : index + 1;
      if (targetIndex < 0 || targetIndex >= current.length) {
        return current;
      }
      const next = [...current];
      [next[index], next[targetIndex]] = [next[targetIndex], next[index]];
      return next;
    });
    setReserveStudyApplyMessage(null);
  };

  const handleDeleteReserveStudyRow = (index: number) => {
    setReserveStudyRows((current) => current.filter((_, rowIndex) => rowIndex !== index));
    setReserveStudyApplyMessage(null);
  };

  const handleSaveReserveStudy = async () => {
    try {
      await ensurePersistedReserveStudySnapshot(activeDraft);
      toast.success('Reserve study saved.');
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to save reserve study rows.'));
    }
  };

  const handleApplyReserveStudy = async () => {
    if (!draftId) {
      toast.error('Create a draft first.');
      return;
    }
    setIsApplyingReserveStudy(true);
    try {
      const persistedDraft = await ensurePersistedDraftSnapshot();
      const persistedReserveDraft = await ensurePersistedReserveStudySnapshot(persistedDraft);
      const response = await applyReserveStudyToBudget(hoaId, persistedReserveDraft.id);
      hydrateDraftState(response.draft);
      onLineItemsUpdate(mapBudgetHistoryLineItems(response.draft.line_items));
      setReserveStudyApplyMessage(response.message);
      if (response.applied_count > 0) {
        toast.success(response.message);
      } else {
        toast.message(response.message);
      }
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to apply reserve study rows to the budget.'));
    } finally {
      setIsApplyingReserveStudy(false);
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

  const budgetFileRequiresAttention =
    bundleUploadResult?.budget_source.status === 'failed' ||
    bundleUploadResult?.budget_source.status === 'review_required';
  const reserveFileRequiresAttention =
    bundleUploadResult?.reserve_study.status === 'failed' ||
    bundleUploadResult?.reserve_study.status === 'review_required';

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
            <div className="flex items-center gap-2">
              <Link to={`/hoa/${hoaId}/disclosure`}>
                <Button
                  variant="outline"
                  size="sm"
                  className="border-[#d4d4d4] text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
                >
                  <PackageCheck className="mr-1.5 h-4 w-4" />
                  Disclosure Package
                </Button>
              </Link>
              <Link to={`/hoa/${hoaId}/settings`}>
              <Button variant="ghost" size="icon" className="hover:bg-[#f5f5f5]">
                <Settings className="h-5 w-5 text-[#525252]" />
              </Button>
              </Link>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-3xl px-8 py-16">
          <div className="rounded-xl border-2 border-dashed border-[#d4d4d4] bg-white p-16 text-center shadow-sm">
            <div className="flex flex-col items-center gap-6">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-[#f5f5f5]">
                <Upload className="h-8 w-8 text-[#525252]" />
              </div>
              <div>
                <h2 className="mb-2 text-xl font-semibold text-[#111111]">Create Budget Draft</h2>
                <p className="text-sm text-[#737373]">
                  {uploadState === 'uploading'
                    ? 'Processing the selected file...'
                    : 'Select a budget source. Add a reserve study PDF if available.'}
                </p>
              </div>
              <div className="w-full rounded-xl border border-[#e5e5e5] bg-[#fafafa] p-5 text-left">
                <div className="mb-5 rounded-xl border border-[#e5e5e5] bg-white p-4">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="mb-3 text-xs font-medium uppercase tracking-[0.18em] text-[#737373]">
                        Assessment Mode
                      </p>
                      <div className="grid gap-3 md:grid-cols-2">
                        {ASSESSMENT_MODE_OPTIONS.map((option) => (
                          <button
                            key={option.value}
                            type="button"
                            onClick={() => setAssessmentMode(option.value)}
                            className={
                              assessmentMode === option.value
                                ? 'rounded-xl border-2 border-[#111111] bg-white p-4 text-left shadow-sm'
                                : 'rounded-xl border border-[#d4d4d4] bg-white p-4 text-left shadow-sm hover:border-[#a3a3a3]'
                            }
                          >
                            <p className="text-sm font-medium text-[#111111]">{option.label}</p>
                            <p className="mt-2 text-xs leading-5 text-[#737373]">{option.description}</p>
                          </button>
                        ))}
                      </div>
                      <p className="mt-4 text-xs leading-5 text-[#737373]">
                        {assessmentModeHelperCopy(assessmentMode)}
                      </p>
                      <p className="mt-2 text-xs leading-5 text-[#737373]">
                        {assessmentModeWorkflowCopy(assessmentMode)}
                      </p>
                    </div>
                    <div className="shrink-0 rounded-lg border border-[#e5e5e5] bg-[#fafafa] px-3 py-2 text-xs text-[#525252]">
                      <span className="block text-[10px] uppercase tracking-[0.18em] text-[#8a8a8a]">
                        Current HOA Mode
                      </span>
                      <span className="mt-1 block font-medium text-[#111111]">
                        {assessmentModeLabel(hoa.assessment_mode)}
                      </span>
                    </div>
                  </div>
                </div>
                <p className="mb-3 text-xs font-medium uppercase tracking-[0.18em] text-[#737373]">
                  Budget Source Mode
                </p>
                <div className="grid gap-3 md:grid-cols-2">
                  {BUDGET_SOURCE_MODE_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => setBudgetSourceMode(option.value)}
                      className={
                        budgetSourceMode === option.value
                          ? 'rounded-xl border-2 border-[#111111] bg-white p-4 text-left shadow-sm'
                          : 'rounded-xl border border-[#d4d4d4] bg-white p-4 text-left shadow-sm hover:border-[#a3a3a3]'
                      }
                    >
                      <p className="text-sm font-medium text-[#111111]">{option.label}</p>
                      <p className="mt-2 text-xs leading-5 text-[#737373]">{option.description}</p>
                    </button>
                  ))}
                </div>
                <p className="mt-4 text-xs leading-5 text-[#737373]">
                  {budgetSourceModeHelperCopy(budgetSourceMode)}
                </p>
              </div>
              <div className="grid w-full gap-4 md:grid-cols-2">
                <FileDropzone
                  title={budgetSourceModeUploadTitle(budgetSourceMode)}
                  helper={budgetSourceModeHelperCopy(budgetSourceMode)}
                  accept=".xlsx,.xls,.pdf"
                  fileName={budgetSourceFile?.name ?? null}
                  disabled={uploadState === 'uploading'}
                  required
                  status={
                    budgetFileRequiresAttention
                      ? 'attention'
                      : budgetSourceFile
                        ? 'selected'
                        : reserveStudyFile
                          ? 'attention'
                          : 'idle'
                  }
                  statusMessage={
                    budgetSourceFile
                      ? 'Selected and ready to upload.'
                      : reserveStudyFile
                        ? 'Budget source is still needed.'
                        : budgetSourceModeUploadPlaceholder(budgetSourceMode)
                  }
                  actionLabel="Select file"
                  onFilesSelected={(files) => setBudgetSourceFile(files?.[0] ?? null)}
                  onClear={() => setBudgetSourceFile(null)}
                />
                <FileDropzone
                  title="Reserve Study PDF"
                  helper="Optional. Attach a reserve study PDF to review reserve components with this draft."
                  accept=".pdf,application/pdf"
                  fileName={reserveStudyFile?.name ?? null}
                  disabled={uploadState === 'uploading'}
                  status={
                    reserveFileRequiresAttention
                      ? 'attention'
                      : reserveStudyFile
                        ? 'selected'
                        : budgetSourceFile
                          ? 'attention'
                          : 'idle'
                  }
                  statusMessage={
                    reserveStudyFile
                      ? 'Selected and ready to upload.'
                      : budgetSourceFile
                        ? 'Add now if available, or continue with budget only.'
                        : 'Separate reserve study PDF'
                  }
                  actionLabel="Select PDF"
                  onFilesSelected={(files) => setReserveStudyFile(files?.[0] ?? null)}
                  onClear={() => setReserveStudyFile(null)}
                />
              </div>
              <Button
                onClick={handleCreateDraftUpload}
                disabled={uploadState === 'uploading' || !budgetSourceFile}
                className="min-w-[240px] bg-[#111111] px-8 py-3 text-base font-medium text-white shadow-sm hover:bg-[#262626]"
              >
                {uploadState === 'uploading'
                  ? 'Processing...'
                  : reserveStudyFile
                    ? 'Create Draft from Files'
                    : 'Create Budget Draft'}
              </Button>
              {bundleUploadResult ? (
                <div className="w-full rounded-xl border border-[#e5e5e5] bg-[#fafafa] p-5 text-left">
                  <h3 className="mb-4 text-sm font-semibold uppercase tracking-[0.18em] text-[#737373]">
                    File Results
                  </h3>
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="rounded-xl border border-[#e5e5e5] bg-white p-4">
                      <p className="text-sm font-medium text-[#111111]">Budget File</p>
                      <p className="mt-1 text-xs text-[#737373]">{bundleUploadResult.budget_source.filename}</p>
                      <p className="mt-3 text-sm text-[#525252]">
                        Status: {bundleUploadResult.budget_source.status}
                      </p>
                      {bundleUploadResult.budget_source.review_reason ? (
                        <p className="mt-2 text-sm text-[#b45309]">
                          {bundleUploadResult.budget_source.review_reason}
                        </p>
                      ) : null}
                      {bundleUploadResult.budget_source.warnings.length > 0 ? (
                        <ul className="mt-3 space-y-2 text-xs leading-5 text-[#525252]">
                          {bundleUploadResult.budget_source.warnings.map((warning, index) => (
                            <li key={`${warning}-${index}`} className="rounded-lg bg-[#fff7ed] px-3 py-2">
                              {warning}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      {renderExtractionDebug(bundleUploadResult.budget_source.debug_info)}
                    </div>
                    <div className="rounded-xl border border-[#e5e5e5] bg-white p-4">
                      <p className="text-sm font-medium text-[#111111]">Reserve Study</p>
                      <p className="mt-1 text-xs text-[#737373]">{bundleUploadResult.reserve_study.filename}</p>
                      <p className="mt-3 text-sm text-[#525252]">
                        Status: {bundleUploadResult.reserve_study.status}
                      </p>
                      {bundleUploadResult.reserve_study.review_reason ? (
                        <p className="mt-2 text-sm text-[#b45309]">
                          {bundleUploadResult.reserve_study.review_reason}
                        </p>
                      ) : null}
                      {bundleUploadResult.reserve_study.warnings.length > 0 ? (
                        <ul className="mt-3 space-y-2 text-xs leading-5 text-[#525252]">
                          {bundleUploadResult.reserve_study.warnings.map((warning, index) => (
                            <li key={`${warning}-${index}`} className="rounded-lg bg-[#fff7ed] px-3 py-2">
                              {warning}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      {renderExtractionDebug(bundleUploadResult.reserve_study.debug_info)}
                    </div>
                  </div>
                  {bundleUploadResult.can_continue_with_budget_only && bundleUploadResult.draft ? (
                    <div className="mt-4 flex flex-wrap gap-3">
                      <Button
                        onClick={handleContinueWithBudgetOnly}
                        className="bg-[#111111] text-white hover:bg-[#262626]"
                      >
                        Continue with Budget Only
                      </Button>
                      <Button
                        variant="outline"
                        onClick={() => setBundleUploadResult(null)}
                        className="border-[#d4d4d4] text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
                      >
                        Choose Another Reserve Study PDF
                      </Button>
                    </div>
                  ) : null}
                  {bundleUploadResult.can_continue_with_reserve_study_only ? (
                    <p className="mt-4 text-sm text-[#525252]">
                      Reserve study upload succeeded, but a budget draft cannot be created until a valid budget file is uploaded.
                    </p>
                  ) : null}
                </div>
              ) : null}
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
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
                    <p className="text-sm text-[#737373]">Fiscal Year: {fiscalYearLabel}</p>
                    <span className="text-[#d4d4d4]">•</span>
                    <p className="text-xs text-[#737373]">Draft {draftId ?? 'Unavailable'}</p>
                    <span className="text-[#d4d4d4]">•</span>
                    <p className="text-xs text-[#737373]">{budgetSourceModeLabel(activeDraft?.source_mode ?? budgetSourceMode)}</p>
                    <span className="text-[#d4d4d4]">•</span>
                    <p className="text-xs text-[#737373]">
                      {assessmentModeLabel(activeDraft?.assessment_mode ?? assessmentMode)}
                    </p>
                  </div>
                </div>
              </div>
            <div className="flex flex-wrap items-center justify-end gap-3">
              <div className="rounded-lg border border-[#e5e5e5] bg-[#fafafa] px-3 py-2 text-right">
                <span className="text-xs text-[#a3a3a3]">Last Saved</span>
                <p className="text-sm font-medium text-[#525252]">
                  {lastSaved ? formatTimestamp(lastSaved) : 'Not saved yet'}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={handleSaveDraft}
                disabled={isSavingDraft || isSavingReserveStudy || isGenerating || !draftId}
                className="border-[#d4d4d4] text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
              >
                {isSavingDraft || isSavingReserveStudy ? 'Saving...' : 'Save Draft'}
              </Button>
              <Label className="cursor-pointer whitespace-nowrap text-xs font-normal text-[#737373]">
                <Checkbox
                  checked={autoSaveEnabled}
                  onCheckedChange={(checked) => setAutoSaveEnabled(checked === true)}
                  disabled={!draftId || isGenerating}
                  className="border-[#a3a3a3] data-[state=checked]:border-[#111111] data-[state=checked]:bg-[#111111]"
                />
                Auto-save every 30 seconds
              </Label>
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
              <Link to={`/hoa/${hoaId}/disclosure`}>
                <Button
                  variant="outline"
                  size="sm"
                  className="border-[#d4d4d4] px-4 font-medium text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
                >
                  <PackageCheck className="mr-1.5 h-4 w-4" />
                  Disclosure Package
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
        <div className="flex items-center justify-between gap-4 bg-white/95 px-8 py-5 backdrop-blur-sm">
          <div className="flex flex-wrap items-center gap-2">
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
              variant={currentView === 'reserve' ? 'default' : 'outline'}
              onClick={() => setCurrentView('reserve')}
              className={
                currentView === 'reserve'
                  ? 'bg-[#111111] text-white shadow-sm hover:bg-[#262626]'
                  : 'border-[#e5e5e5] text-[#525252] hover:border-[#737373] hover:bg-[#f5f5f5]'
              }
            >
              Reserve Study
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
              <div className="mb-8 rounded-xl border border-[#e5e5e5] bg-white p-4 shadow-sm">
                <DraftBaselineComparePanel
                  hoaId={hoaId}
                  draftId={draftId}
                  onPersistDraftSnapshot={ensurePersistedDraftSnapshot}
                  isPersistingDraft={isSavingDraft}
                  isGenerating={isGenerating}
                />
              </div>
            ) : null}
            {draftId ? (
              <div className="mb-8">
                <GLMergeSuggestions
                  suggestions={visibleGlMergeSuggestions}
                  merges={glMerges}
                  loading={glMergeSuggestionsLoading}
                  error={glMergeSuggestionsError}
                  actionLoading={glMergeActionLoading}
                  unmergingApplicationId={unmergingApplicationId}
                  onSuggest={() => void handleFetchGlMergeSuggestions()}
                  onApplySuggestion={(suggestion) => void handleApplyMergeSuggestion(suggestion)}
                  onModifySuggestion={handleModifyMergeSuggestion}
                  onDismissSuggestion={handleDismissMergeSuggestion}
                  onUnmerge={(merge) => void handleUnmergeMerge(merge)}
                />
              </div>
            ) : null}
            <EnrichedView
              hoaId={hoaId}
              draftId={draftId}
              lineItems={lineItems}
              onPercentChange={handlePercentChange}
              onFieldChange={handleLineItemFieldChange}
              onNoteSaved={handleNoteSaved}
              onRequestMerge={handleRequestMerge}
              onReadOnlyOverride={handleReadOnlyOverride}
              units={hoa.units}
              reserveInflationRate={activeReserveInflationRate}
              hasUnsavedChanges={hasUnsavedDraftChanges}
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
        {currentView === 'reserve' && (() => {
          const reserveStudyTable = (
            <ReserveStudyView
              rows={reserveStudyRows}
              warnings={reserveStudyWarnings}
              status={reserveStudyStatus}
              onRowChange={handleReserveStudyRowChange}
              onAddRow={handleAddReserveStudyRow}
              onAddHeader={handleAddReserveStudyHeader}
              onMoveRow={handleMoveReserveStudyRow}
              onDeleteRow={handleDeleteReserveStudyRow}
              onSave={handleSaveReserveStudy}
              onApply={handleApplyReserveStudy}
              onReplaceFile={draftId ? handleReplaceReserveFile : undefined}
              hasUnsavedChanges={hasUnsavedReserveStudyChanges}
              isSaving={isSavingReserveStudy}
              isApplying={isApplyingReserveStudy}
              applyMessage={reserveStudyApplyMessage}
              onJumpToPage={jumpToReservePage}
              onOpenCompare={reserveStudyUploadId ? () => setIsReserveCompareOpen(true) : undefined}
            />
          );
          return isReserveCompareOpen && reserveStudyUploadId ? (
            <DrePdfCompareView
              fileUrl={reserveStudyFileUrl(hoaId, reserveStudyUploadId)}
              targetPage={reserveTargetPage}
              onClose={() => setIsReserveCompareOpen(false)}
            >
              {reserveStudyTable}
            </DrePdfCompareView>
          ) : (
            reserveStudyTable
          );
        })()}
      </main>
      <AlertDialog
        open={glMergeDialog.primaryId !== null}
        onOpenChange={(open) => {
          if (!open) {
            closeMergeDialog();
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Merge Similar Budget Rows</AlertDialogTitle>
            <AlertDialogDescription>
              Pick the secondary row to absorb into the surviving primary row. The primary label
              stays the same and the active draft updates immediately.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-4">
            <div className="rounded-lg border border-[#e5e5e5] bg-[#fafafa] p-4">
              <p className="text-xs font-medium uppercase tracking-wide text-[#737373]">Primary row</p>
              <p className="mt-1 text-sm font-semibold text-[#111111]">
                {selectedMergePrimary?.name ?? 'Choose a row from the budget table'}
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="merge-secondary">Merge with</Label>
              <Select
                value={glMergeDialog.secondaryId ?? undefined}
                onValueChange={(value) =>
                  setGlMergeDialog((current) => ({ ...current, secondaryId: value }))
                }
              >
                <SelectTrigger id="merge-secondary" className="bg-white border-[#e5e5e5]">
                  <SelectValue placeholder="Choose another row in the same section" />
                </SelectTrigger>
                <SelectContent>
                  {mergeCandidates.map((candidate) => (
                    <SelectItem key={candidate.id} value={candidate.id}>
                      {candidate.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {selectedMergePreview ? (
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-[#e5e5e5] bg-white p-3">
                  <p className="text-xs uppercase tracking-wide text-[#737373]">YTD total</p>
                  <p className="mt-1 text-sm font-semibold text-[#111111]">
                    {formatCurrency(selectedMergePreview.ytdActual)}
                  </p>
                </div>
                <div className="rounded-lg border border-[#e5e5e5] bg-white p-3">
                  <p className="text-xs uppercase tracking-wide text-[#737373]">Annual budget</p>
                  <p className="mt-1 text-sm font-semibold text-[#111111]">
                    {formatCurrency(selectedMergePreview.annualBudget)}
                  </p>
                </div>
                <div className="rounded-lg border border-[#e5e5e5] bg-white p-3">
                  <p className="text-xs uppercase tracking-wide text-[#737373]">Projection</p>
                  <p className="mt-1 text-sm font-semibold text-[#111111]">
                    {formatCurrency(selectedMergePreview.projection)}
                  </p>
                </div>
                <div className="rounded-lg border border-[#e5e5e5] bg-white p-3">
                  <p className="text-xs uppercase tracking-wide text-[#737373]">Current proposed total</p>
                  <p className="mt-1 text-sm font-semibold text-[#111111]">
                    {formatCurrency(selectedMergePreview.proposed)}
                  </p>
                </div>
              </div>
            ) : null}
          </div>
          <AlertDialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={closeMergeDialog}
              className="border-[#d4d4d4] hover:bg-[#f5f5f5]"
            >
              Cancel
            </Button>
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault();
                void handleCommitSelectedMerge();
              }}
              disabled={!selectedMergePrimary || !selectedMergeSecondary || glMergeActionLoading}
              className="bg-[#111111] text-white hover:bg-[#262626]"
            >
              {glMergeActionLoading ? 'Merging...' : 'Commit Merge'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
