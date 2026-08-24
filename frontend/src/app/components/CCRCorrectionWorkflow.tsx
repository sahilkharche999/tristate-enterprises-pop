import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  approveCCRRun,
  getCCRPromotionPreview,
  reopenAndRepromoteCCRRun,
  saveCCRCorrectionOperation,
  saveCCRScalarCorrection,
  saveCCRUnitFactors,
  type CCRPoolCorrectionOperation,
  type CCRPromotionPreview,
  type CCRRecommendedOperation,
  type CCRSetupType,
  type CCRUnitFactorEntry,
} from '../api/ccr';
import type { DREExtractionRunDetail } from '../api/dre';
import {
  buildAdvancedFactorPayload,
  buildCCRCorrectionAction,
  buildCCRFactorPayload,
  buildCCRExtractedDetail,
  buildCCRReadySummary,
  buildIssueCard,
  ccrIssueIdentity,
  correctionActionLabel,
  executeCCRCorrection,
  friendlyCCRError,
  isCCRApprovalDisabled,
  isUsableCCRRecommendation,
  mergeExtractionForDetail,
  type CCRCorrectionResult,
  type CCRFactorDraft,
} from '../lib/ccrReviewWorkflow';
import { CCRAdvancedCorrections } from './CCRAdvancedCorrections';
import { CCRExtractedDetail } from './CCRExtractedDetail';
import { cn } from './ui/utils';

type Props = {
  hoaId: number;
  runId: number;
  detail: DREExtractionRunDetail;
  setupType: CCRSetupType;
  onSetupTypeChange: (value: CCRSetupType) => void;
  onCompare: () => void;
  jumpToPage: (page: number) => void;
  onRunChanged: () => Promise<void> | void;
};

const SETUP_CHOICES: Record<CCRSetupType, string> = {
  fixed: 'Every home pays the same amount',
  grouped: 'Homes pay by their documented group',
  per_unit: 'Each home has its own documented share',
};

const EMPTY_FACTOR_ROW: CCRFactorDraft = {
  unit_number: '',
  square_feet: '',
  ownership_percent: '',
};

function resolvedCategories(preview: CCRPromotionPreview | null) {
  const value = preview?.resolved_extraction?.allocation_pools;
  return Array.isArray(value) ? (value as Array<Record<string, unknown>>) : [];
}

export function CCRCorrectionWorkflow({
  hoaId,
  runId,
  detail,
  setupType,
  onSetupTypeChange,
  onCompare,
  jumpToPage,
  onRunChanged,
}: Props) {
  const requestIdentity = `${hoaId}:${runId}:${setupType}`;
  const identityRef = useRef(requestIdentity);
  identityRef.current = requestIdentity;
  const requestSequenceRef = useRef(0);
  const [previewData, setPreview] = useState<CCRPromotionPreview | null>(null);
  const [previewRevision, setPreviewRevision] = useState(0);
  const [loadedIdentity, setLoadedIdentity] = useState<string | null>(null);
  const preview =
    loadedIdentity === requestIdentity ? previewData : null;
  const requiresPerUnit = resolvedCategories(preview).some(
    (category) =>
      String(category.recipient_scope || 'all_units') !== 'all_units' ||
      [
        'square_footage',
        'ownership_percentage',
        'custom_factor',
        'specified_value',
      ].includes(String(category.allocation_method || '')),
  );
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ownershipChoice, setOwnershipChoice] = useState<'fraction' | 'points'>('points');
  const [factorRows, setFactorRows] = useState<CCRFactorDraft[]>([]);
  const [sourcePageDrafts, setSourcePageDrafts] = useState<Record<string, string>>({});
  const [refreshNeeded, setRefreshNeeded] = useState<
    'correction' | 'approval' | null
  >(null);
  const [approvalSucceeded, setApprovalSucceeded] = useState(false);

  const loadPreview = useCallback(async () => {
    const sequence = ++requestSequenceRef.current;
    const next = await getCCRPromotionPreview(hoaId, runId, setupType);
    if (
      identityRef.current !== requestIdentity ||
      requestSequenceRef.current !== sequence
    ) {
      throw new Error('Preview request was superseded.');
    }
    setPreview(next);
    setPreviewRevision((current) => current + 1);
    setLoadedIdentity(requestIdentity);
    return next;
  }, [hoaId, requestIdentity, runId, setupType]);

  useEffect(() => {
    if (!preview || factorRows.length > 0) return;
    const hasMissingFactors = preview.issues.some(
      (issue) =>
        issue.code === 'CCR_UNIT_FACTORS_MISSING' ||
        issue.code === 'CCR_SPECIFIED_VALUES_MISSING' ||
        issue.code === 'CCR_SPECIFIED_VALUES_INVALID',
    );
    if (!hasMissingFactors) return;
    const unitStructure = preview.resolved_extraction?.unit_structure as
      | { units?: Array<Record<string, unknown>> }
      | undefined;
    const units = Array.isArray(unitStructure?.units) ? unitStructure.units : [];
    setFactorRows(
      units.length > 0
        ? units.map((unit) => ({
            unit_number: String(unit.unit_number || ''),
            square_feet:
              unit.square_feet == null ? '' : String(unit.square_feet),
            ownership_percent:
              unit.ownership_percent == null
                ? ''
                : String(unit.ownership_percent),
            fixed_amounts: Object.fromEntries(
              (Array.isArray(unit.pool_factors) ? unit.pool_factors : [])
                .filter(
                  (factor) =>
                    typeof factor === 'object' &&
                    factor != null &&
                    String((factor as Record<string, unknown>).factor_type) ===
                      'dollar_amount',
                )
                .map((factor) => {
                  const value = factor as Record<string, unknown>;
                  return [
                    String(value.pool_key || ''),
                    value.factor_value == null
                      ? ''
                      : String(value.factor_value),
                  ];
                }),
            ),
            custom_factors: Object.fromEntries(
              (Array.isArray(unit.pool_factors) ? unit.pool_factors : [])
                .filter(
                  (factor) =>
                    typeof factor === 'object' &&
                    factor != null &&
                    String((factor as Record<string, unknown>).factor_type) !==
                      'dollar_amount',
                )
                .map((factor) => {
                  const value = factor as Record<string, unknown>;
                  return [
                    String(value.pool_key || ''),
                    value.factor_value == null
                      ? ''
                      : String(value.factor_value),
                  ];
                }),
            ),
          }))
        : [{ ...EMPTY_FACTOR_ROW }],
    );
  }, [factorRows.length, preview]);

  useEffect(() => {
    if (requiresPerUnit && setupType !== 'per_unit') {
      onSetupTypeChange('per_unit');
    }
  }, [onSetupTypeChange, requiresPerUnit, setupType]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    setPreview(null);
    setPreviewRevision(0);
    setLoadedIdentity(null);
    setFactorRows([]);
    setSourcePageDrafts({});
    setRefreshNeeded(null);
    setApprovalSucceeded(false);
    loadPreview()
      .catch((exc) => {
        if (active) {
          setError(
            friendlyCCRError(
              exc,
              'We could not load this review. Refresh the page and try again.',
            ),
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [loadPreview]);

  const dependencies = useMemo(
    () => ({
      saveOperation: (operation: CCRPoolCorrectionOperation) =>
        saveCCRCorrectionOperation(hoaId, runId, operation),
      saveScalar: (correction: {
        field_path: string;
        old_value?: unknown;
        new_value: unknown;
      }) => saveCCRScalarCorrection(hoaId, runId, correction),
      saveFactors: (factors: CCRUnitFactorEntry[]) =>
        saveCCRUnitFactors(hoaId, runId, factors),
      refetchPreview: loadPreview,
    }),
    [hoaId, loadPreview, runId],
  );

  function acceptCorrectionResult(result: CCRCorrectionResult) {
    if (identityRef.current !== requestIdentity) return;
    if (result.status === 'refreshed') {
      setPreview(result.preview);
      setRefreshNeeded(null);
      return;
    }
    setRefreshNeeded('correction');
  }

  async function retryRefresh() {
    setBusy('refresh');
    setError(null);
    try {
      await loadPreview();
      if (refreshNeeded === 'approval') {
        await onRunChanged();
      }
      setRefreshNeeded(null);
    } catch (exc) {
      setError(
        friendlyCCRError(
          exc,
          'The save succeeded, but the latest review still could not be loaded. Try refresh again.',
        ),
      );
    } finally {
      setBusy(null);
    }
  }

  async function applyRecommendation(
    issueIndex: number,
    recommendation: CCRRecommendedOperation,
  ) {
    let action = buildCCRCorrectionAction(
      recommendation,
      preview?.review_version ?? 0,
      preview?.resolved_extraction ?? null,
    );
    if (recommendation.operation === 'set_ownership_percent_form') {
      action = {
        kind: 'scalar',
        fieldPath: 'unit_structure.ownership_percent_form',
        value: ownershipChoice,
      };
    }
    if (!action) {
      setError(
        'This item needs a little more information before it can be saved.',
      );
      return;
    }
    setBusy(`issue-${issueIndex}`);
    setError(null);
    try {
      acceptCorrectionResult(
        await executeCCRCorrection(action, dependencies),
      );
    } catch (exc) {
      setError(friendlyCCRError(exc));
    } finally {
      setBusy(null);
    }
  }

  async function saveMissingFactors(issueIndex: number) {
    const issue = preview?.issues[issueIndex];
    const category = resolvedCategories(preview).find(
      (row) => row.pool_key === issue?.category_key,
    );
    const usesOwnership = category?.allocation_method === 'ownership_percentage';
    const allocationMethod = String(category?.allocation_method || '');
    const unitStructure = preview?.resolved_extraction?.unit_structure as
      | { unit_count?: unknown }
      | undefined;
    const parsedUnitCount = Number(unitStructure?.unit_count);
    const expectedUnitCount =
      Number.isInteger(parsedUnitCount) && parsedUnitCount > 0
        ? parsedUnitCount
        : null;
    const categoryKey = String(issue?.category_key || '');
    const participantUnitNumbers =
      category?.recipient_scope === 'all_units'
        ? undefined
        : (
            (Array.isArray(category?.selected_unit_numbers)
              ? category.selected_unit_numbers
              : category?.participant_unit_numbers) as unknown[] | undefined
          )?.map(String);
    const payload =
      allocationMethod === 'custom_factor' ||
      allocationMethod === 'specified_value'
        ? buildAdvancedFactorPayload(factorRows, expectedUnitCount, {
            squareFeet: false,
            ownershipPercent: false,
            fixedCategoryKeys:
              allocationMethod === 'specified_value' && categoryKey
                ? [categoryKey]
                : [],
            fixedRecipientUnitNumbers:
              allocationMethod === 'specified_value' &&
              categoryKey &&
              participantUnitNumbers
                ? { [categoryKey]: participantUnitNumbers }
                : undefined,
            customCategoryKeys:
              allocationMethod === 'custom_factor' && categoryKey
                ? [categoryKey]
                : [],
            customRecipientUnitNumbers:
              allocationMethod === 'custom_factor' &&
              categoryKey &&
              participantUnitNumbers
                ? { [categoryKey]: participantUnitNumbers }
                : undefined,
          })
        : buildCCRFactorPayload(
            factorRows,
            expectedUnitCount,
            usesOwnership ? 'ownership_percent' : 'square_feet',
          );
    if (payload.error) {
      setError(payload.error);
      return;
    }
    setBusy(`issue-${issueIndex}`);
    setError(null);
    try {
      const fresh = await executeCCRCorrection(
        { kind: 'factors', values: payload.values },
        dependencies,
      );
      acceptCorrectionResult(fresh);
    } catch (exc) {
      setError(friendlyCCRError(exc));
    } finally {
      setBusy(null);
    }
  }

  async function saveSourcePages(issueIndex: number) {
    const issue = preview?.issues[issueIndex];
    const issueIdentity = issue ? ccrIssueIdentity(issue) : '';
    const pages = String(sourcePageDrafts[issueIdentity] || '')
      .split(/[\s,]+/)
      .filter(Boolean)
      .map(Number);
    if (
      !issue?.category_key ||
      pages.length === 0 ||
      pages.some((page) => !Number.isInteger(page) || page <= 0)
    ) {
      setError('Enter one or more valid PDF page numbers.');
      return;
    }
    setBusy(`issue-${issueIndex}`);
    setError(null);
    try {
      const result = await executeCCRCorrection(
        {
          kind: 'operation',
          value: {
            operation: 'update',
            base_version: preview?.review_version ?? 0,
            category_key: issue.category_key,
            changes: { source_pages: pages },
          },
        },
        dependencies,
      );
      acceptCorrectionResult(result);
    } catch (exc) {
      setError(friendlyCCRError(exc));
    } finally {
      setBusy(null);
    }
  }

  async function saveAdvancedCorrection(
    operation: CCRPoolCorrectionOperation,
    reason: string,
    factors?: CCRUnitFactorEntry[],
  ) {
    setBusy('advanced');
    setError(null);
    try {
      if (factors) {
        await saveCCRCorrectionOperation(hoaId, runId, operation, reason);
        try {
          await saveCCRUnitFactors(hoaId, runId, factors);
        } catch (exc) {
          setRefreshNeeded('correction');
          setError(
            friendlyCCRError(
              exc,
              'The category was saved, but the home values still need attention. Refresh the review before trying again.',
            ),
          );
          return false;
        }
        try {
          const next = await loadPreview();
          acceptCorrectionResult({ status: 'refreshed', preview: next });
        } catch (refreshError) {
          acceptCorrectionResult({
            status: 'saved_refresh_failed',
            refreshError,
          });
        }
        return true;
      }
      const result = await executeCCRCorrection(
        { kind: 'operation', value: operation },
        {
          ...dependencies,
          saveOperation: (value) =>
            saveCCRCorrectionOperation(hoaId, runId, value, reason),
        },
      );
      acceptCorrectionResult(result);
      return true;
    } catch (exc) {
      setError(friendlyCCRError(exc));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function approve() {
    if (isCCRApprovalDisabled(preview, busy !== null)) return;
    const message =
      detail.review_status === 'promoted'
        ? 'Apply these reviewed corrections to the active owner-charge setup?'
        : 'Approve these owner-charge instructions?';
    if (!confirm(message)) return;
    setBusy('approve');
    setError(null);
    try {
      if (detail.review_status === 'promoted') {
        await reopenAndRepromoteCCRRun(hoaId, runId, setupType);
      } else {
        await approveCCRRun(hoaId, runId, setupType);
      }
    } catch (exc) {
      setError(
        friendlyCCRError(
          exc,
          'We could not approve these instructions. Refresh the review and try again.',
        ),
      );
      setBusy(null);
      return;
    }

    setApprovalSucceeded(true);
    try {
      await loadPreview();
      await onRunChanged();
      setRefreshNeeded(null);
    } catch {
      setRefreshNeeded('approval');
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <section className="p-4" aria-live="polite">
        <h1 className="text-xl font-semibold text-slate-900">Review owner charges</h1>
        <p className="mt-3 text-sm text-slate-600">Preparing the latest corrected review…</p>
      </section>
    );
  }

  if (!preview) {
    return (
      <section className="space-y-3 p-4">
        <h1 className="text-xl font-semibold text-slate-900">Review owner charges</h1>
        <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
          {error || 'We could not load this review. Refresh the page and try again.'}
        </div>
      </section>
    );
  }

  const issueCards = preview.issues.map((issue) =>
    buildIssueCard(issue, preview.resolved_extraction),
  );
  const ready = buildCCRReadySummary(preview.resolved_extraction);
  const categories = resolvedCategories(preview);
  const extractedDetail = buildCCRExtractedDetail(
    mergeExtractionForDetail(
      preview.resolved_extraction,
      detail.parsed_json,
    ),
  );

  return (
    <section className="space-y-6 p-4">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Review owner charges</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-600">
            Work through the decisions below, compare each one with the PDF, then approve the reviewed instructions.
          </p>
        </div>
        <button
          type="button"
          onClick={onCompare}
          className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50"
        >
          Compare with PDF
        </button>
      </header>

      {error && (
        <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
          {error}
        </div>
      )}

      {refreshNeeded && (
        <div
          role="status"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-950"
        >
          <p className="font-medium">
            {refreshNeeded === 'approval'
              ? 'Owner charges approved. Refresh needed.'
              : 'Correction saved. Refresh needed.'}
          </p>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void retryRefresh()}
            className="rounded-md border border-blue-300 bg-white px-3 py-1.5 font-semibold text-blue-900 hover:bg-blue-100 disabled:opacity-50"
          >
            {busy === 'refresh' ? 'Refreshing…' : 'Retry refresh'}
          </button>
        </div>
      )}

      {preview.issues.length > 0 ? (
        <section aria-labelledby="ccr-attention-heading">
          <div className="flex flex-wrap items-end justify-between gap-2">
            <div>
              <h2 id="ccr-attention-heading" className="text-lg font-semibold text-slate-900">
                What needs attention
              </h2>
              <p className="mt-1 text-sm text-slate-600">
                {preview.issues.length} decision{preview.issues.length === 1 ? '' : 's'} before approval.
                These values are not printed as a complete table in the CC&R, such as a DRE schedule or parking assignments.
              </p>
            </div>
          </div>
          <div className="mt-3 grid gap-4 lg:grid-cols-2">
            {preview.issues.map((issue, index) => {
              const issueIdentity = ccrIssueIdentity(issue);
              const card = issueCards[index];
              const recommendation = issue.recommended_operation;
              const usableRecommendation =
                recommendation &&
                isUsableCCRRecommendation(
                  recommendation,
                  preview.resolved_extraction,
                );
              const actionLabel = usableRecommendation
                ? correctionActionLabel(recommendation)
                : null;
              const isOwnershipChoice =
                recommendation?.operation === 'set_ownership_percent_form';
              const needsSourcePages = issue.code === 'CCR_POOL_SOURCE_MISSING';
              const category = categories.find(
                (row) => row.pool_key === issue.category_key,
              );
              const needsFactors =
                (issue.code === 'CCR_UNIT_FACTORS_MISSING' ||
                  issue.code === 'CCR_SPECIFIED_VALUES_MISSING' ||
                  issue.code === 'CCR_SPECIFIED_VALUES_INVALID') &&
                Boolean(issue.category_key) &&
                Boolean(category);
              const usesOwnership =
                category?.allocation_method === 'ownership_percentage';
              const usesCustomFactor =
                category?.allocation_method === 'custom_factor';
              const usesFixedAmount =
                category?.allocation_method === 'specified_value';
              return (
                <article key={issueIdentity} className="rounded-xl border border-amber-200 bg-amber-50/40 p-4 shadow-sm">
                  <h3 className="font-semibold text-slate-950">{card.heading}</h3>
                  <dl className="mt-3 space-y-3 text-sm">
                    <div>
                      <dt className="font-medium text-slate-800">What happened</dt>
                      <dd className="mt-0.5 text-slate-650">{card.whatHappened}</dd>
                    </div>
                    <div>
                      <dt className="font-medium text-slate-800">Why owner charges are affected</dt>
                      <dd className="mt-0.5 text-slate-650">{card.ownerImpact}</dd>
                    </div>
                    <div>
                      <dt className="font-medium text-slate-800">Recommended correction</dt>
                      <dd className="mt-0.5 text-slate-650">{card.recommendation}</dd>
                    </div>
                  </dl>

                  {card.evidence.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2" aria-label="PDF evidence">
                      {card.evidence.map(({ page, label }) => (
                        <button
                          key={page}
                          type="button"
                          onClick={() => jumpToPage(page)}
                          className="rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  )}

                  {isOwnershipChoice && (
                    <fieldset className="mt-4">
                      <legend className="text-sm font-medium text-slate-800">How are the percentages printed?</legend>
                      <div className="mt-2 flex flex-wrap gap-3 text-sm">
                        <label className="flex items-center gap-1.5">
                          <input
                            type="radio"
                            name={`ownership-format-${index}`}
                            checked={ownershipChoice === 'points'}
                            onChange={() => setOwnershipChoice('points')}
                          />
                          As percentages, such as 3.47
                        </label>
                        <label className="flex items-center gap-1.5">
                          <input
                            type="radio"
                            name={`ownership-format-${index}`}
                            checked={ownershipChoice === 'fraction'}
                            onChange={() => setOwnershipChoice('fraction')}
                          />
                          As decimals, such as 0.0347
                        </label>
                      </div>
                    </fieldset>
                  )}

                  {needsSourcePages && (
                    <div className="mt-4">
                      <label
                        htmlFor={`source-pages-${index}`}
                        className="text-sm font-medium text-slate-800"
                      >
                        PDF page numbers
                      </label>
                      <p
                        id={`source-pages-help-${index}`}
                        className="mt-0.5 text-xs text-slate-600"
                      >
                        Enter one or more pages, separated by commas.
                      </p>
                      <input
                        id={`source-pages-${index}`}
                        aria-describedby={`source-pages-help-${index}`}
                        value={sourcePageDrafts[issueIdentity] || ''}
                        onChange={(event) =>
                          setSourcePageDrafts((current) => ({
                            ...current,
                            [issueIdentity]: event.target.value,
                          }))
                        }
                        inputMode="numeric"
                        placeholder="For example: 4, 8"
                        className="mt-2 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                      />
                    </div>
                  )}

                  {needsFactors && (
                    <div className="mt-4 space-y-2">
                      <p className="text-sm font-medium text-slate-800">
                        Enter the missing values for each home
                      </p>
                      <div className="space-y-2">
                        {factorRows.map((row, rowIndex) => (
                          <fieldset
                            key={rowIndex}
                            className="grid gap-2 rounded-md border border-slate-200 bg-white p-3 sm:grid-cols-2"
                          >
                            <legend className="px-1 text-xs font-medium text-slate-600">
                              Home {rowIndex + 1}
                            </legend>
                            <label className="text-xs font-medium text-slate-700">
                              Home identifier
                              <input
                                type="text"
                                aria-label={`Home identifier ${rowIndex + 1}`}
                                autoComplete="off"
                                value={row.unit_number}
                                onChange={(event) =>
                                  setFactorRows((current) =>
                                    current.map((item, itemIndex) =>
                                      itemIndex === rowIndex
                                        ? {
                                            ...item,
                                            unit_number: event.target.value,
                                          }
                                        : item,
                                    ),
                                  )
                                }
                                className="mt-1 w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
                              />
                            </label>
                            <label className="text-xs font-medium text-slate-700">
                              {usesOwnership
                                ? 'Ownership percentage'
                                : usesCustomFactor
                                  ? 'Custom factor'
                                  : usesFixedAmount
                                    ? 'Fixed annual amount'
                                    : 'Square feet'}
                            <input
                              type="number"
                              aria-label={`${
                                usesOwnership
                                  ? 'Ownership percentage'
                                  : usesCustomFactor
                                    ? 'Custom factor'
                                    : usesFixedAmount
                                      ? 'Fixed annual amount'
                                      : 'Square feet'
                              } for home ${rowIndex + 1}`}
                              min="0"
                              inputMode="decimal"
                              value={
                                usesOwnership
                                  ? row.ownership_percent
                                  : usesCustomFactor
                                    ? row.custom_factors?.[
                                        String(issue.category_key || '')
                                      ] || ''
                                    : usesFixedAmount
                                      ? row.fixed_amounts?.[
                                          String(issue.category_key || '')
                                        ] || ''
                                      : row.square_feet
                              }
                              onChange={(event) =>
                                setFactorRows((current) =>
                                  current.map((item, itemIndex) =>
                                    itemIndex === rowIndex
                                      ? usesCustomFactor
                                        ? {
                                            ...item,
                                            custom_factors: {
                                              ...item.custom_factors,
                                              [String(issue.category_key || '')]:
                                                event.target.value,
                                            },
                                          }
                                        : usesFixedAmount
                                          ? {
                                              ...item,
                                              fixed_amounts: {
                                                ...item.fixed_amounts,
                                                [String(issue.category_key || '')]:
                                                  event.target.value,
                                              },
                                            }
                                          : {
                                              ...item,
                                              [usesOwnership
                                                ? 'ownership_percent'
                                                : 'square_feet']:
                                                event.target.value,
                                            }
                                      : item,
                                  ),
                                )
                              }
                              className="mt-1 w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
                            />
                            </label>
                          </fieldset>
                        ))}
                      </div>
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() =>
                          setFactorRows((current) => [
                            ...current,
                            { ...EMPTY_FACTOR_ROW },
                          ])
                        }
                        className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      >
                        Add another home
                      </button>
                    </div>
                  )}

                  <div className="mt-4">
                    {refreshNeeded ? (
                      <p className="text-sm font-medium text-blue-900">
                        Your save is complete. Refresh the review before making another correction.
                      </p>
                    ) : needsFactors ? (
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() => void saveMissingFactors(index)}
                        className="rounded-md bg-slate-900 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {busy === `issue-${index}`
                          ? 'Saving home values…'
                          : 'Save home values'}
                      </button>
                    ) : needsSourcePages ? (
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() => void saveSourcePages(index)}
                        className="rounded-md bg-slate-900 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {busy === `issue-${index}`
                          ? 'Saving PDF pages…'
                          : 'Save PDF pages'}
                      </button>
                    ) : usableRecommendation && actionLabel ? (
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() => void applyRecommendation(index, recommendation)}
                        className="rounded-md bg-slate-900 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {busy === `issue-${index}` ? 'Saving correction…' : actionLabel}
                      </button>
                    ) : (
                      <p className="text-sm text-slate-600">
                        More details are needed before this can be corrected.
                        Use Advanced corrections when that editor is available.
                      </p>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      ) : (
        <section className="rounded-xl border border-emerald-200 bg-emerald-50 p-5" aria-labelledby="ccr-ready-heading">
          <h2 id="ccr-ready-heading" className="text-lg font-semibold text-emerald-950">
            Ready to approve
          </h2>
          <dl className="mt-3 grid gap-3 text-sm md:grid-cols-2">
            <div>
              <dt className="font-medium text-emerald-950">What is charged</dt>
              <dd className="mt-0.5 text-emerald-900">{ready.charged}</dd>
            </div>
            <div>
              <dt className="font-medium text-emerald-950">Who pays</dt>
              <dd className="mt-0.5 text-emerald-900">{ready.whoPays}</dd>
            </div>
            <div>
              <dt className="font-medium text-emerald-950">How it is divided</dt>
              <dd className="mt-0.5 text-emerald-900">{ready.howDivided}</dd>
            </div>
            <div>
              <dt className="font-medium text-emerald-950">When it is billed</dt>
              <dd className="mt-0.5 text-emerald-900">{ready.whenBilled}</dd>
            </div>
          </dl>
        </section>
      )}

      {extractedDetail ? (
        <CCRExtractedDetail detail={extractedDetail} jumpToPage={jumpToPage} />
      ) : null}

      <CCRAdvancedCorrections
        categories={categories}
        unitStructure={
          preview.resolved_extraction?.unit_structure as
            | {
                unit_count?: unknown;
                units?: Array<Record<string, unknown>>;
              }
            | undefined
        }
        previewIdentity={`${runId}:${preview.extraction_run_id}`}
        previewRevision={previewRevision}
        reviewVersion={preview.review_version}
        disabled={busy !== null || refreshNeeded !== null}
        jumpToPage={jumpToPage}
        onSave={saveAdvancedCorrection}
      />

      <section className="flex flex-wrap items-end justify-between gap-4 rounded-xl border border-slate-200 bg-white p-4">
        <label className="text-sm font-medium text-slate-800">
          How owner shares are organized
          <select
            aria-label="How owner shares are organized"
            value={setupType}
            disabled={busy !== null}
            onChange={(event) => onSetupTypeChange(event.target.value as CCRSetupType)}
            className="mt-1 block rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
          >
            {(Object.keys(SETUP_CHOICES) as CCRSetupType[]).map((choice) => (
              <option
                key={choice}
                value={choice}
                disabled={requiresPerUnit && choice !== 'per_unit'}
              >
                {SETUP_CHOICES[choice]}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={
            approvalSucceeded ||
            refreshNeeded !== null ||
            isCCRApprovalDisabled(preview, busy !== null)
          }
          onClick={() => void approve()}
          className={cn(
            'rounded-md px-4 py-2 text-sm font-semibold text-white',
            approvalSucceeded ||
              refreshNeeded !== null ||
              isCCRApprovalDisabled(preview, busy !== null)
              ? 'cursor-not-allowed bg-slate-300'
              : 'bg-emerald-700 hover:bg-emerald-800',
          )}
        >
          {approvalSucceeded
            ? 'Approved'
            : busy === 'approve'
            ? 'Approving…'
            : detail.review_status === 'promoted'
              ? 'Apply approved corrections'
              : 'Approve owner charges'}
        </button>
      </section>
    </section>
  );
}
