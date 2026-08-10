import { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { toast } from 'sonner';

import { BudgetScreen } from './BudgetScreen';
import { GeneratedBudgetScreen } from './GeneratedBudgetScreen';
import {
  reopenBudgetVersion,
  generateBudgetVersion,
  getBudgetDraft,
  getActiveBudgetDraft,
  getBudgetHistory,
  getBudgetVersion,
  mapBudgetHistoryLineItems,
  mapEditorLineItemsToBudgetHistory,
  type BudgetDraftPayload,
  type BudgetVersionDetail,
  type BudgetVersionSummary,
} from '../api/budgetHistory';
import { getHOA, type HOARecord } from '../api/hoa';
import type { SheetTable } from '../api/macros';
import { type AISuggestionResponse, type LineItem } from '../data/mockData';
import { getErrorMessage } from '../lib/errors';

// The Phase 11 hardcode that gated generation on `hoa.name === "Old Mill
// Homeowners Association"` was retired by the DRE-driven assessment-engine
// work — every HOA with a promoted assessment_setup + a finalized
// AnnualPackage is now supported. The backend re-runs preflight on every
// generate call (see disclosure_package/preflight.py) and returns a
// failure block via the existing DisclosureFailureBlock path when
// prerequisites aren't met, so the UI gate is purely an affordance hint.

export function BudgetScreenWrapper() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const showGeneratedBudget = searchParams.get('generated') === 'true';
  const versionId = searchParams.get('versionId');
  const draftId = searchParams.get('draftId');
  const readOnly = searchParams.get('readOnly') === '1';
  const forceCreateDraft = searchParams.get('create') === '1';
  const initialView = (searchParams.get('view') || 'enriched') as 'enriched' | 'budget' | 'ai';

  const [hoa, setHoa] = useState<HOARecord | null>(null);
  const [isHoaLoading, setIsHoaLoading] = useState(true);
  const [hoaError, setHoaError] = useState<string | null>(null);
  const [lineItems, setLineItems] = useState<LineItem[]>([]);
  const [activeDraft, setActiveDraft] = useState<BudgetDraftPayload | null>(null);
  const [generatedVersion, setGeneratedVersion] = useState<BudgetVersionDetail | null>(null);
  const [latestVersionSummary, setLatestVersionSummary] = useState<BudgetVersionSummary | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isReopening, setIsReopening] = useState(false);
  const [aiResponse, setAiResponse] = useState<AISuggestionResponse | null>(null);
  const reopenedFromVersionId = activeDraft?.reopened_from_version_id ?? null;

  // Load persisted suggestions on mount: sessionStorage first (instant), then DB fallback.
  useEffect(() => {
    if (!id) return;
    try {
      const cached = sessionStorage.getItem(`ai-suggestions-${id}`);
      if (cached) {
        setAiResponse(JSON.parse(cached));
        return;
      }
    } catch { /* ignore */ }
    import('../api/macros').then(({ getLatestAISuggestions }) =>
      getLatestAISuggestions(id)
        .then((resp) => { if (resp) setAiResponse(resp); })
        .catch(() => { /* non-blocking — no suggestions yet is fine */ })
    );
  }, [id]);

  // Keep sessionStorage in sync for fast within-session re-renders.
  useEffect(() => {
    if (!id) return;
    if (aiResponse) {
      try { sessionStorage.setItem(`ai-suggestions-${id}`, JSON.stringify(aiResponse)); } catch { /* quota */ }
    } else {
      sessionStorage.removeItem(`ai-suggestions-${id}`);
    }
  }, [id, aiResponse]);

  useEffect(() => {
    let cancelled = false;

    async function loadBudgetContext() {
      if (!id) {
        setHoaError('HOA not found');
        setIsHoaLoading(false);
        return;
      }

      setIsHoaLoading(true);
      setHoaError(null);

      try {
        const selectedHoa = await getHOA(id);
        if (cancelled) return;
        setHoa(selectedHoa);

        let draft: BudgetDraftPayload | null = null;
        if (draftId) {
          draft = await getBudgetDraft(id, draftId);
        } else {
          try {
            draft = await getActiveBudgetDraft(id);
          } catch (error) {
            if (!(error && typeof error === 'object' && 'status' in error && error.status === 404)) {
              throw error;
            }
          }
        }
        if (cancelled) return;
        setActiveDraft(draft);

        // History is used for honest empty-state when no active draft exists.
        let latestSummary: BudgetVersionSummary | null = null;
        try {
          const history = await getBudgetHistory(id);
          if (!cancelled) {
            latestSummary = history.versions?.[0] ?? null;
            setLatestVersionSummary(latestSummary);
          }
        } catch {
          if (!cancelled) {
            setLatestVersionSummary(null);
          }
        }

        if (versionId) {
          const version = await getBudgetVersion(id, versionId);
          if (cancelled) return;
          setGeneratedVersion(version);
          setLineItems(mapBudgetHistoryLineItems(version.line_items));
        } else if (
          !draft &&
          !forceCreateDraft &&
          latestSummary &&
          !showGeneratedBudget
        ) {
          // No active draft but a generated version exists — open latest
          // generated view instead of empty Create Budget Draft upload.
          const version = await getBudgetVersion(id, latestSummary.id);
          if (cancelled) return;
          setGeneratedVersion(version);
          setLineItems(mapBudgetHistoryLineItems(version.line_items));
          const params = new URLSearchParams();
          params.set('generated', 'true');
          params.set('versionId', String(latestSummary.id));
          params.set('readOnly', '1');
          setSearchParams(params, { replace: true });
        } else {
          setGeneratedVersion(null);
          setLineItems(draft ? mapBudgetHistoryLineItems(draft.line_items) : []);
        }
      } catch (error) {
        if (!cancelled) {
          setHoaError(getErrorMessage(error, 'Failed to load HOA budget context.'));
        }
      } finally {
        if (!cancelled) {
          setIsHoaLoading(false);
        }
      }
    }

    void loadBudgetContext();
    return () => {
      cancelled = true;
    };
  }, [draftId, forceCreateDraft, id, setSearchParams, showGeneratedBudget, versionId]);

  const handleDraftChange = (draft: BudgetDraftPayload) => {
    setActiveDraft(draft);
    setLineItems(mapBudgetHistoryLineItems(draft.line_items));
  };

  const handleDraftDeleted = () => {
    setActiveDraft(null);
    setLineItems([]);
    setGeneratedVersion(null);
    setSearchParams(new URLSearchParams());
    toast.success('Draft discarded.');
  };

  const updateGeneratedSearchParams = (nextVersionId: number, nextReadOnly: boolean) => {
    const params = new URLSearchParams();
    params.set('generated', 'true');
    params.set('versionId', String(nextVersionId));
    if (nextReadOnly) {
      params.set('readOnly', '1');
    }
    setSearchParams(params);
  };

  const handleGenerateBudget = async (payload: {
    draftId: number;
    lineItems: LineItem[];
    globalNote: string;
    statementMonth: number | null;
    growthFactor: number | null;
    growthFactorNote: string;
  }) => {
    if (!id) return;

    setIsGenerating(true);
    try {
      const response = await generateBudgetVersion(id, {
        draft_id: payload.draftId,
        line_items: mapEditorLineItemsToBudgetHistory(payload.lineItems),
        global_note: payload.globalNote || null,
      });
      setActiveDraft(response.draft);
      setGeneratedVersion(response.version);
      setLineItems(mapBudgetHistoryLineItems(response.version.line_items));
      updateGeneratedSearchParams(response.version.id, false);
      toast.success(`${response.version.version_code} generated successfully.`);
    } finally {
      setIsGenerating(false);
    }
  };

  const handleRegenerateSnapshot = async () => {
    if (!activeDraft) {
      toast.error('This historical version is read-only. Reopen flows land in the next plan.');
      return;
    }

    await handleGenerateBudget({
      draftId: activeDraft.id,
      lineItems,
      globalNote: activeDraft.global_note ?? '',
      statementMonth: activeDraft.statement_month ?? null,
      growthFactor: activeDraft.growth_factor ?? null,
      growthFactorNote: activeDraft.growth_factor_note ?? '',
    });
  };

  const handleReopenAsDraft = async () => {
    if (!id || !generatedVersion) {
      toast.error('No generated version available to reopen.');
      return;
    }
    setIsReopening(true);
    try {
      const response = await reopenBudgetVersion(id, generatedVersion.id);
      setActiveDraft(response.draft);
      setLineItems(mapBudgetHistoryLineItems(response.draft.line_items));
      const params = new URLSearchParams();
      params.set('view', 'enriched');
      params.set('draftId', String(response.draft.id));
      setSearchParams(params);
      toast.success('Opened a new editable draft from this version.');
    } catch (error) {
      toast.error(getErrorMessage(error, 'Failed to reopen this version as a draft.'));
    } finally {
      setIsReopening(false);
    }
  };

  const handleBackToDraft = async () => {
    if (!id) {
      return;
    }

    // Active draft exists — open the editor for that draft.
    if (activeDraft?.status === 'active') {
      const params = new URLSearchParams();
      params.set('view', 'enriched');
      params.set('draftId', String(activeDraft.id));
      setSearchParams(params);
      return;
    }

    // Writable generated version (not read-only): reopen as a new editable draft.
    if (generatedVersion && !readOnly) {
      await handleReopenAsDraft();
      return;
    }

    // Read-only latest-generated (typical after Generate) or no draft to open:
    // leave the HOA. Clearing params alone re-triggers auto-open of the latest
    // generated version and traps the operator in a back-button loop.
    navigate('/workspace');
  };

  if (isHoaLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white">
        <p className="text-[#666666]">Loading HOA budget context...</p>
      </div>
    );
  }

  if (hoaError || !hoa || !id) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white">
        <p className="text-[#666666]">{hoaError || 'HOA not found'}</p>
      </div>
    );
  }

  if (showGeneratedBudget) {
    if (!generatedVersion) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-white">
          <p className="text-[#666666]">Generated version not found.</p>
        </div>
      );
    }

    return (
      <GeneratedBudgetScreen
        hoa={hoa}
        hoaId={id}
        lineItems={lineItems}
        versionCode={generatedVersion.version_code}
        versionId={generatedVersion.id}
        stage={generatedVersion.stage}
        label={generatedVersion.label ?? null}
        createdAt={generatedVersion.created_at}
        sourceUploadFilename={
          generatedVersion.source_upload_filename ?? activeDraft?.upload_filename ?? undefined
        }
        sourceMode={generatedVersion.source_mode ?? activeDraft?.source_mode ?? null}
        onRegenerateSnapshot={handleRegenerateSnapshot}
        onBackToDraft={handleBackToDraft}
        onReopenAsDraft={handleReopenAsDraft}
        budgetPreview={
          generatedVersion.budget_preview && typeof generatedVersion.budget_preview === 'object'
            ? (generatedVersion.budget_preview as SheetTable)
            : null
        }
        growthFactor={generatedVersion.growth_factor ?? undefined}
        growthFactorNote={generatedVersion.growth_factor_note ?? undefined}
        reserveInflationRate={generatedVersion.reserve_inflation_rate}
        isRegenerating={isGenerating}
        isReopening={isReopening}
        readOnly={readOnly}
      />
    );
  }

  return (
    <BudgetScreen
      hoa={hoa}
      hoaId={id}
      lineItems={lineItems}
      onLineItemsUpdate={setLineItems}
      onGenerateBudget={handleGenerateBudget}
      onDraftChange={handleDraftChange}
      onDraftDeleted={handleDraftDeleted}
      budgetGenerated={Boolean(generatedVersion)}
      isGenerating={isGenerating}
      initialView={initialView}
      savedAiResponse={aiResponse}
      onAiResponseChange={setAiResponse}
      activeDraft={activeDraft}
      latestVersionSummary={latestVersionSummary}
      key={reopenedFromVersionId ? `reopened-${reopenedFromVersionId}-${activeDraft?.id ?? 'none'}` : 'active-draft'}
    />
  );
}
