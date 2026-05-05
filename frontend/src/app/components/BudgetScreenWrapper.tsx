import { useEffect, useState } from 'react';
import { useParams, useSearchParams } from 'react-router';
import { toast } from 'sonner';

import { BudgetScreen } from './BudgetScreen';
import { GeneratedBudgetScreen } from './GeneratedBudgetScreen';
import {
  generateBudgetVersion,
  getBudgetDraft,
  getActiveBudgetDraft,
  getBudgetVersion,
  mapBudgetHistoryLineItems,
  mapEditorLineItemsToBudgetHistory,
  type BudgetDraftPayload,
  type BudgetVersionDetail,
} from '../api/budgetHistory';
import { getHOA, type HOARecord } from '../api/hoa';
import type { SheetTable } from '../api/macros';
import { initialLineItems, type AISuggestionResponse, type LineItem } from '../data/mockData';
import { getErrorMessage } from '../lib/errors';

export function BudgetScreenWrapper() {
  const { id } = useParams<{ id: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const showGeneratedBudget = searchParams.get('generated') === 'true';
  const versionId = searchParams.get('versionId');
  const draftId = searchParams.get('draftId');
  const readOnly = searchParams.get('readOnly') === '1';
  const initialView = (searchParams.get('view') || 'enriched') as 'enriched' | 'budget' | 'ai';

  const [hoa, setHoa] = useState<HOARecord | null>(null);
  const [isHoaLoading, setIsHoaLoading] = useState(true);
  const [hoaError, setHoaError] = useState<string | null>(null);
  const [lineItems, setLineItems] = useState<LineItem[]>(initialLineItems);
  const [activeDraft, setActiveDraft] = useState<BudgetDraftPayload | null>(null);
  const [generatedVersion, setGeneratedVersion] = useState<BudgetVersionDetail | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [aiResponse, setAiResponse] = useState<AISuggestionResponse | null>(() => {
    if (!id) return null;
    try {
      const cached = sessionStorage.getItem(`ai-suggestions-${id}`);
      return cached ? JSON.parse(cached) : null;
    } catch {
      return null;
    }
  });
  const reopenedFromVersionId = activeDraft?.reopened_from_version_id ?? null;

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

        if (versionId) {
          const version = await getBudgetVersion(id, versionId);
          if (cancelled) return;
          setGeneratedVersion(version);
          setLineItems(mapBudgetHistoryLineItems(version.line_items));
        } else {
          setGeneratedVersion(null);
          setLineItems(draft ? mapBudgetHistoryLineItems(draft.line_items) : initialLineItems);
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
  }, [draftId, id, versionId]);

  const handleDraftChange = (draft: BudgetDraftPayload) => {
    setActiveDraft(draft);
    setLineItems(mapBudgetHistoryLineItems(draft.line_items));
  };

  const handleDraftDeleted = () => {
    setActiveDraft(null);
    setLineItems(initialLineItems);
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

  const handleBackToDraft = async () => {
    if (!id) {
      return;
    }

    if (activeDraft?.status === 'active') {
      const params = new URLSearchParams();
      params.set('view', 'enriched');
      params.set('draftId', String(activeDraft.id));
      setSearchParams(params);
      return;
    }

    if (generatedVersion && !readOnly) {
      try {
        const response = await reopenBudgetVersion(id, generatedVersion.id);
        setActiveDraft(response.draft);
        setLineItems(mapBudgetHistoryLineItems(response.draft.line_items));
        const params = new URLSearchParams();
        params.set('view', 'enriched');
        params.set('draftId', String(response.draft.id));
        setSearchParams(params);
        toast.success('Opened a new editable draft from the generated version.');
        return;
      } catch (error) {
        toast.error(getErrorMessage(error, 'Failed to reopen this version as a draft.'));
      }
    }

    const params = new URLSearchParams();
    params.set('view', 'enriched');
    setSearchParams(params);
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
        onRegenerateSnapshot={handleRegenerateSnapshot}
        onBackToDraft={handleBackToDraft}
        budgetPreview={
          generatedVersion.budget_preview && typeof generatedVersion.budget_preview === 'object'
            ? (generatedVersion.budget_preview as SheetTable)
            : null
        }
        growthFactor={generatedVersion.growth_factor ?? undefined}
        growthFactorNote={generatedVersion.growth_factor_note ?? undefined}
        reserveInflationRate={generatedVersion.reserve_inflation_rate}
        isRegenerating={isGenerating}
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
      key={reopenedFromVersionId ? `reopened-${reopenedFromVersionId}-${activeDraft?.id ?? 'none'}` : 'active-draft'}
    />
  );
}
