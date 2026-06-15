import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import { ArrowLeft, Ban, Check, Pencil, Play, RotateCw, Save, Sparkles, X } from 'lucide-react';

import {
  analyzeAssessmentMappingReview,
  applyAssessmentMappings,
  approveMappingRule,
  approveResidualRouting,
  assignAssessmentMappingReviewRow,
  createMappingAlias,
  disableMappingRule,
  editMappingRule,
  getAssessmentMappingReview,
  rejectMappingRule,
  revokeMappingAlias,
  setAssessmentMappingReviewRowDisposition,
  setExemptionDecision,
  type MappingReviewAnalysis,
  type MappingReviewState,
  type ReviewRow,
} from '../api/assessmentMappingReview';
import { formatCurrency } from '../lib/budget';
import { getErrorMessage } from '../lib/errors';

function humanize(value: string) {
  return value.replaceAll('_', ' ');
}

function StatusBadge({ value }: { value: string }) {
  const cls = value === 'approved' || value === 'ready' || value === 'mapped'
    ? 'bg-emerald-50 text-emerald-700 ring-emerald-600/20'
    : value === 'rejected' || value === 'conflict'
      ? 'bg-rose-50 text-rose-700 ring-rose-600/20'
      : 'bg-amber-50 text-amber-700 ring-amber-600/20';
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${cls}`}>{humanize(value)}</span>;
}

function DecisionBadge({ value }: { value: string }) {
  const cls = value === 'safe_suggestion'
    ? 'bg-emerald-50 text-emerald-700 ring-emerald-600/20'
    : value === 'review_required_suggestion'
      ? 'bg-amber-50 text-amber-700 ring-amber-600/20'
      : 'bg-slate-50 text-slate-700 ring-slate-600/20';
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${cls}`}>{humanize(value)}</span>;
}

function countLines(groups: MappingReviewState['eligibility_groups']) {
  return Object.values(groups).reduce((sum, lines) => sum + lines.length, 0);
}

function recommendationText(row: ReviewRow) {
  const candidate = row.candidates[0];
  if (!candidate) return 'No recommendation';
  return `${candidate.pool_name} (${candidate.score.toFixed(2)})`;
}

function recommendationLabel(row: ReviewRow) {
  const candidate = row.candidates[0];
  if (!candidate) return null;
  if (candidate.budget_line_derivation === 'residual_default') return 'Residual/base suggestion';
  if (candidate.rule_source === 'ai') return 'AI suggestion';
  return 'Rule suggestion';
}

type AnalysisSubject = {
  line_label: string;
  normalized_label: string;
  account_code: string | null;
};

type InlineAnalysisHint = {
  title: string;
  detail: string;
  badge: string;
};

const MAIN_BLOCKER_CATEGORIES = new Set(['unresolved_eligible_lines', 'pending_split']);

function analysisCacheKey(hoaId: number) {
  return `assessment-mapping-analysis-${hoaId}`;
}

function readCachedAnalysis(hoaId: number): MappingReviewAnalysis | null {
  if (!Number.isFinite(hoaId)) return null;
  if (typeof sessionStorage === 'undefined') return null;
  try {
    const cached = sessionStorage.getItem(analysisCacheKey(hoaId));
    return cached ? JSON.parse(cached) : null;
  } catch {
    return null;
  }
}

function matchesAnalysisSubject(row: ReviewRow, item: AnalysisSubject) {
  const sameLabel = item.normalized_label === row.normalized_label || item.line_label === row.line_label;
  const sameAccount = !item.account_code || !row.account_code || item.account_code === row.account_code;
  return sameLabel && sameAccount;
}

function inlineAnalysisHint(row: ReviewRow, analysis: MappingReviewAnalysis | null): InlineAnalysisHint | null {
  if (!analysis?.available) return null;

  const safe = analysis.safe_to_stage.find((item) => matchesAnalysisSubject(row, item));
  if (safe) {
    return {
      title: `AI: ${humanize(safe.suggested_pool_key)}`,
      detail: safe.explanation,
      badge: safe.action_kind,
    };
  }

  const decision = analysis.needs_decision.find((item) => matchesAnalysisSubject(row, item));
  if (decision) {
    const prefix = decision.recommended_pool_key ? `Recommended: ${humanize(decision.recommended_pool_key)}. ` : '';
    return {
      title: 'AI needs decision',
      detail: `${prefix}${decision.explanation}`,
      badge: decision.blocker_kind,
    };
  }

  const exclusion = analysis.exclude_from_mapping.find((item) => matchesAnalysisSubject(row, item));
  if (exclusion) {
    return {
      title: 'AI: exclude from mapping',
      detail: exclusion.explanation,
      badge: exclusion.exclusion_kind,
    };
  }

  const residual = analysis.residual_equal_preview.candidate_lines.find((item) => matchesAnalysisSubject(row, item));
  if (residual) {
    return {
      title: analysis.residual_equal_preview.residual_pool_key
        ? `AI residual: ${humanize(analysis.residual_equal_preview.residual_pool_key)}`
        : 'AI residual preview',
      detail: residual.reason || analysis.residual_equal_preview.explanation,
      badge: 'residual_equal_preview',
    };
  }

  return null;
}

export function AssessmentMappingReviewScreen() {
  const { id } = useParams<{ id: string }>();
  const hoaId = Number(id);
  const [state, setState] = useState<MappingReviewState | null>(null);
  const [analysisState, setAnalysisState] = useState<{ hoaId: number; analysis: MappingReviewAnalysis | null }>(() => ({
    hoaId,
    analysis: readCachedAnalysis(hoaId),
  }));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editingRuleId, setEditingRuleId] = useState<number | null>(null);
  const [editLabel, setEditLabel] = useState('');
  const [aliasDraft, setAliasDraft] = useState({ pool_key: '', dre_label: '', budget_label: '' });
  const [exemptionNotes, setExemptionNotes] = useState<Record<string, string>>({});
  const [rowPoolSelections, setRowPoolSelections] = useState<Record<string, string>>({});
  const analysis = analysisState.hoaId === hoaId ? analysisState.analysis : readCachedAnalysis(hoaId);

  async function load() {
    if (!Number.isFinite(hoaId)) return;
    setBusy('load');
    setError(null);
    try {
      setState(await getAssessmentMappingReview(hoaId));
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to load mapping review.'));
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hoaId]);

  useEffect(() => {
    if (!state) return;
    setRowPoolSelections((prev) => {
      const next: Record<string, string> = {};
      for (const row of state.review_rows) {
        next[row.line_key] = prev[row.line_key]
          ?? row.current_pool_key
          ?? row.recommended_pool_key
          ?? row.valid_pool_options[0]?.pool_key
          ?? '';
      }
      return next;
    });
  }, [state]);

  const totals = useMemo(() => {
    if (!state) return { lines: 0, rules: 0, mapped: 0 };
    return {
      lines: countLines(state.eligibility_groups),
      rules: state.rules.length,
      mapped: state.review_rows.filter((row) => row.current_status === 'mapped').length,
    };
  }, [state]);

  const showAnalyzeButton = useMemo(() => {
    if (!state) return false;
    return state.progress.unresolved_count > 0 || Boolean(analysis);
  }, [analysis, state]);

  const blockerEntries = useMemo(() => {
    if (!state) {
      return {
        main: [] as Array<[string, string[]]>,
        diagnostics: [] as Array<[string, string[]]>,
      };
    }
    const main = Object.entries(state.mapping_review_blockers).filter(([category]) => MAIN_BLOCKER_CATEGORIES.has(category));
    if (state.reconciliation_summary.reconciliation_failures.length > 0) {
      main.push(['reconciliation_failures', state.reconciliation_summary.reconciliation_failures]);
    }
    return {
      main,
      diagnostics: Object.entries(state.mapping_review_blockers).filter(([category]) => !MAIN_BLOCKER_CATEGORIES.has(category)),
    };
  }, [state]);

  async function runAction(label: string, action: () => Promise<unknown>) {
    setBusy(label);
    setError(null);
    try {
      await action();
      await load();
    } catch (err) {
      setError(getErrorMessage(err, 'Action failed.'));
      setBusy(null);
    }
  }

  async function runAnalyze() {
    setBusy('analyze');
    setError(null);
    try {
      const nextAnalysis = await analyzeAssessmentMappingReview(hoaId);
      try {
        sessionStorage.setItem(analysisCacheKey(hoaId), JSON.stringify(nextAnalysis));
      } catch {
        // Cache is best-effort; mapping workflow must still work if storage is full.
      }
      setAnalysisState({ hoaId, analysis: nextAnalysis });
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to analyze mapping review.'));
    } finally {
      setBusy(null);
    }
  }

  function startEdit(rule: MappingReviewState['rules'][number]) {
    setEditingRuleId(rule.id);
    setEditLabel(rule.match_label || rule.normalized_label || '');
  }

  async function handleAssign(row: ReviewRow) {
    const poolKey = rowPoolSelections[row.line_key];
    if (!poolKey) return;
    await runAction(`assign-${row.line_key}`, () => assignAssessmentMappingReviewRow(hoaId, {
      line_key: row.line_key,
      pool_key: poolKey,
    }));
  }

  async function handleDisposition(row: ReviewRow, dispositionState: 'excluded_non_regular' | 'reserve_detail' | 'pending_split' | 'clear') {
    await runAction(`${dispositionState}-${row.line_key}`, () => setAssessmentMappingReviewRowDisposition(hoaId, {
      line_key: row.line_key,
      disposition_state: dispositionState,
    }));
  }

  if (!id || !Number.isFinite(hoaId)) {
    return <div className="min-h-screen bg-white p-8 text-[#525252]">HOA not found</div>;
  }

  return (
    <div className="min-h-screen bg-[#fafafa]">
      <header className="sticky top-0 z-10 border-b border-[#e5e5e5] bg-white">
        <div className="flex flex-col gap-4 px-5 py-5 md:flex-row md:items-center md:justify-between md:px-8">
          <div className="flex min-w-0 items-center gap-4">
            <Link to={`/hoa/${id}/disclosure`} className="rounded-lg p-2 hover:bg-[#f5f5f5]" aria-label="Back">
              <ArrowLeft className="h-5 w-5 text-[#525252]" />
            </Link>
            <div>
              <h1 className="text-xl font-semibold text-[#111111]">Assessment Mapping Review</h1>
              <p className="text-sm text-[#737373]">Review only eligible current-year rows, assign pools inline, and block final rendering until the filtered regular basis reconciles.</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to={`/hoa/${id}/disclosure`}
              className="rounded-lg border border-[#d4d4d4] px-4 py-2 text-sm font-medium text-[#111111] hover:bg-[#f5f5f5]"
            >
              Go to Disclosure Package
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-6 px-5 py-6 md:px-8">
        {error && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>
        )}

        {!state ? (
          <div className="rounded-lg border border-[#e5e5e5] bg-white p-6 text-sm text-[#666666]">
            Loading mapping review...
          </div>
        ) : (
          <>
            <section className="grid gap-3 md:grid-cols-4">
              {[
                ['Review rows', state.review_rows.length],
                ['Mapped rows', totals.mapped],
                ['Budget lines', totals.lines],
                ['Unresolved', state.progress.unresolved_count],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-[#e5e5e5] bg-white p-4">
                  <p className="text-xs font-medium uppercase text-[#737373]">{label}</p>
                  <p className="mt-1 text-2xl font-semibold text-[#111111]">{value}</p>
                </div>
              ))}
            </section>

            <section className="rounded-lg border border-[#e5e5e5] bg-white p-5">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-lg font-semibold text-[#111111]">Reconciliation Summary</h2>
                <StatusBadge value={state.reconciliation_summary.final_render_blocked ? 'pending_review' : 'ready'} />
              </div>
              <div className="grid gap-3 md:grid-cols-5">
                <div className="rounded-lg border border-[#eeeeee] p-3">
                  <p className="text-xs uppercase text-[#737373]">Mapped regular</p>
                  <p className="mt-1 font-semibold text-[#111111]">{formatCurrency(state.reconciliation_summary.mapped_regular_total)}</p>
                </div>
                <div className="rounded-lg border border-[#eeeeee] p-3">
                  <p className="text-xs uppercase text-[#737373]">Pending split</p>
                  <p className="mt-1 font-semibold text-[#111111]">{formatCurrency(state.reconciliation_summary.pending_split_total)}</p>
                </div>
                <div className="rounded-lg border border-[#eeeeee] p-3">
                  <p className="text-xs uppercase text-[#737373]">Excluded / non-regular</p>
                  <p className="mt-1 font-semibold text-[#111111]">{formatCurrency(state.reconciliation_summary.excluded_non_regular_total)}</p>
                </div>
                <div className="rounded-lg border border-[#eeeeee] p-3">
                  <p className="text-xs uppercase text-[#737373]">Target basis</p>
                  <p className="mt-1 font-semibold text-[#111111]">{formatCurrency(state.reconciliation_summary.target_regular_assessment_basis)}</p>
                </div>
                <div className="rounded-lg border border-[#eeeeee] p-3">
                  <p className="text-xs uppercase text-[#737373]">Difference</p>
                  <p className="mt-1 font-semibold text-[#111111]">{formatCurrency(state.reconciliation_summary.difference)}</p>
                </div>
              </div>
              {blockerEntries.main.length > 0 && (
                <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
                  <p className="font-medium">Current blockers</p>
                  <div className="mt-2 space-y-2">
                    {blockerEntries.main.map(([category, values]) => (
                      <p key={category}>
                        <strong>{humanize(category)}:</strong> {values.join(', ')}
                      </p>
                    ))}
                  </div>
                </div>
              )}
            </section>

            <section className="rounded-lg border border-[#e5e5e5] bg-white p-5">
              <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-[#111111]">Review Table</h2>
                  <p className="text-sm text-[#737373]">Primary workflow: review rows, choose pool, or mark a special current-year disposition.</p>
                </div>
                <div className="flex items-center gap-2">
                  {showAnalyzeButton && (
                    <button
                      type="button"
                      onClick={() => void runAnalyze()}
                      className="inline-flex items-center gap-2 rounded-lg border border-[#d4d4d4] px-3 py-1.5 text-sm font-medium text-[#111111] hover:bg-[#f5f5f5] disabled:opacity-60"
                      disabled={busy !== null}
                    >
                      <Sparkles className="h-4 w-4" />
                      {analysis ? 'Run AI Again' : 'Run AI Recommendations'}
                    </button>
                  )}
                  <button type="button" onClick={() => void load()} className="rounded border border-[#d4d4d4] p-1.5" aria-label="Refresh">
                    <RotateCw className="h-4 w-4" />
                  </button>
                </div>
              </div>
              {analysis && !analysis.available && (
                <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                  {(analysis.reasons.length > 0 ? analysis.reasons : ['AI analysis is not available right now.']).join(', ')}
                </div>
              )}

              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-[#e5e5e5] text-sm">
                  <thead className="text-left text-xs uppercase text-[#737373]">
                    <tr>
                      <th className="py-2 pr-4">Row</th>
                      <th className="py-2 pr-4">Amount</th>
                      <th className="py-2 pr-4">Role</th>
                      <th className="py-2 pr-4">Recommendation</th>
                      <th className="py-2 pr-4">Assignment</th>
                      <th className="py-2 pr-4">Status</th>
                      <th className="py-2 pr-4">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#eeeeee]">
                    {state.review_rows.map((row) => {
                      const selectedPoolKey = rowPoolSelections[row.line_key] || '';
                      const isRegularRow = row.row_role === 'current_year_operating_budget_line';
                      const recommendationKind = recommendationLabel(row);
                      const analysisHint = inlineAnalysisHint(row, analysis);
                      return (
                        <tr key={row.line_key} className="align-top">
                          <td className="py-3 pr-4">
                            <div className="font-medium text-[#111111]">{row.line_label}</div>
                            <div className="mt-1 text-xs text-[#737373]">{row.reason}</div>
                            {row.disposition_note && (
                              <div className="mt-1 text-xs text-[#737373]">Note: {row.disposition_note}</div>
                            )}
                          </td>
                          <td className="py-3 pr-4">
                            <div className="font-medium text-[#111111]">
                              {row.assessment_mapping_amount == null ? '-' : formatCurrency(row.assessment_mapping_amount)}
                            </div>
                            <div className="mt-1 text-xs text-[#737373]">Source: {row.source_column_used}</div>
                          </td>
                          <td className="py-3 pr-4">
                            <div className="text-[#111111]">{humanize(row.row_role)}</div>
                            <div className="mt-1 text-xs text-[#737373]">
                              {row.included_in_regular_basis ? 'Included in regular basis' : 'Outside regular basis'}
                            </div>
                          </td>
                          <td className="py-3 pr-4">
                            <div className="text-[#111111]">{recommendationText(row)}</div>
                            {row.candidates[0] && (
                              <div className="mt-1 flex flex-wrap items-center gap-2">
                                {recommendationKind && (
                                  <span className="rounded-full bg-sky-50 px-2 py-0.5 text-xs font-medium text-sky-700 ring-1 ring-inset ring-sky-600/20">
                                    {recommendationKind}
                                  </span>
                                )}
                                <DecisionBadge value={row.candidates[0].decision_level} />
                                {row.candidates[0].review_reason && (
                                  <span className="text-xs text-amber-700">{row.candidates[0].review_reason}</span>
                                )}
                              </div>
                            )}
                            {analysisHint && (
                              <div className="mt-2 rounded border border-sky-200 bg-sky-50 p-2">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="text-xs font-medium text-sky-800">{analysisHint.title}</span>
                                  <DecisionBadge value={analysisHint.badge} />
                                </div>
                                <p className="mt-1 text-xs text-sky-900">{analysisHint.detail}</p>
                              </div>
                            )}
                          </td>
                          <td className="py-3 pr-4">
                            {isRegularRow ? (
                              <div className="flex min-w-[240px] gap-2">
                                <select
                                  value={selectedPoolKey}
                                  onChange={(event) => setRowPoolSelections((prev) => ({
                                    ...prev,
                                    [row.line_key]: event.target.value,
                                  }))}
                                  className="min-w-0 flex-1 rounded border border-[#d4d4d4] px-2 py-1 text-sm"
                                >
                                  <option value="">Select pool</option>
                                  {row.valid_pool_options.map((option) => (
                                    <option key={`${row.line_key}-${option.pool_key}`} value={option.pool_key}>
                                      {option.pool_name}
                                    </option>
                                  ))}
                                </select>
                                <button
                                  type="button"
                                  className="rounded border border-[#d4d4d4] px-3 py-1 text-sm hover:bg-[#f5f5f5] disabled:opacity-60"
                                  disabled={busy !== null || !selectedPoolKey}
                                  onClick={() => void handleAssign(row)}
                                >
                                  Assign
                                </button>
                              </div>
                            ) : (
                              <span className="text-[#737373]">Not a regular mapping row</span>
                            )}
                            {row.current_pool_key && (
                              <div className="mt-2 text-xs text-[#737373]">Current pool: {row.current_pool_key}</div>
                            )}
                          </td>
                          <td className="py-3 pr-4">
                            <StatusBadge value={row.current_status} />
                          </td>
                          <td className="py-3 pr-4">
                            <div className="flex flex-wrap gap-2">
                              <button
                                type="button"
                                className="rounded border border-[#d4d4d4] px-2 py-1 text-xs hover:bg-[#f5f5f5]"
                                onClick={() => void handleDisposition(row, 'excluded_non_regular')}
                                disabled={busy !== null}
                              >
                                Exclude
                              </button>
                              <button
                                type="button"
                                className="rounded border border-[#d4d4d4] px-2 py-1 text-xs hover:bg-[#f5f5f5]"
                                onClick={() => void handleDisposition(row, 'reserve_detail')}
                                disabled={busy !== null}
                              >
                                Reserve detail
                              </button>
                              <button
                                type="button"
                                className="rounded border border-[#d4d4d4] px-2 py-1 text-xs hover:bg-[#f5f5f5]"
                                onClick={() => void handleDisposition(row, 'pending_split')}
                                disabled={busy !== null}
                              >
                                Needs split
                              </button>
                              <button
                                type="button"
                                className="rounded border border-[#d4d4d4] px-2 py-1 text-xs hover:bg-[#f5f5f5]"
                                onClick={() => void handleDisposition(row, 'clear')}
                                disabled={busy !== null}
                              >
                                Clear
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>

            <details className="rounded-lg border border-[#e5e5e5] bg-[#fcfcfc] p-4">
              <summary className="cursor-pointer text-sm font-medium text-[#111111]">
                Diagnostics and legacy tools
              </summary>
              <p className="mt-2 text-sm text-[#737373]">
                Rules, aliases, exemptions, and residual preview explain suggestions. Row assignments above control completion.
              </p>
              {blockerEntries.diagnostics.length > 0 && (
                <div className="mt-4 rounded-lg border border-[#eeeeee] bg-white p-3 text-sm text-[#525252]">
                  <p className="font-medium text-[#111111]">Diagnostic blockers</p>
                  <div className="mt-2 space-y-2">
                    {blockerEntries.diagnostics.map(([category, values]) => (
                      <p key={category}>
                        <strong>{humanize(category)}:</strong> {values.join(', ')}
                      </p>
                    ))}
                  </div>
                </div>
              )}
              <div className="mt-5 space-y-6">

            <section className="rounded-lg border border-[#e5e5e5] bg-white p-5">
              <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <h2 className="text-lg font-semibold text-[#111111]">Rules</h2>
                <button
                  type="button"
                  onClick={() => void runAction('apply', () => applyAssessmentMappings(hoaId))}
                  className="inline-flex items-center gap-2 rounded-lg border border-[#d4d4d4] px-3 py-1.5 text-sm font-medium text-[#111111] hover:bg-[#f5f5f5] disabled:opacity-60"
                  disabled={busy !== null}
                >
                  <Play className="h-4 w-4" />
                  Apply approved rules
                </button>
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-[#e5e5e5] text-sm">
                  <thead className="text-left text-xs uppercase text-[#737373]">
                    <tr>
                      <th className="py-2 pr-4">Pool</th>
                      <th className="py-2 pr-4">Label</th>
                      <th className="py-2 pr-4">Match</th>
                      <th className="py-2 pr-4">State</th>
                      <th className="py-2 pr-4">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#eeeeee]">
                    {state.rules.map((rule) => (
                      <tr key={rule.id}>
                        <td className="py-3 pr-4 font-medium text-[#111111]">{rule.pool_key}</td>
                        <td className="py-3 pr-4 text-[#525252]">
                          <div>{rule.match_label || rule.normalized_label || 'Residual/default'}</div>
                          {(rule.source_parent_category || rule.source_evidence_text) && (
                            <div className="mt-1 text-xs text-[#737373]">
                              {[rule.source_parent_category, rule.source_evidence_text].filter(Boolean).join(' • ')}
                            </div>
                          )}
                        </td>
                        <td className="py-3 pr-4 text-[#525252]">{rule.match_type}</td>
                        <td className="py-3 pr-4"><StatusBadge value={rule.approval_status} /></td>
                        <td className="py-3 pr-4">
                          <div className="flex gap-2">
                            <button type="button" className="rounded border border-[#d4d4d4] p-1.5 hover:bg-[#f5f5f5]" onClick={() => void runAction(`approve-${rule.id}`, () => approveMappingRule(hoaId, rule.id))} aria-label="Approve rule">
                              <Check className="h-4 w-4" />
                            </button>
                            <button type="button" className="rounded border border-[#d4d4d4] p-1.5 hover:bg-[#f5f5f5]" onClick={() => void runAction(`reject-${rule.id}`, () => rejectMappingRule(hoaId, rule.id))} aria-label="Reject rule">
                              <X className="h-4 w-4" />
                            </button>
                            <button type="button" className="rounded border border-[#d4d4d4] p-1.5 hover:bg-[#f5f5f5]" onClick={() => void runAction(`disable-${rule.id}`, () => disableMappingRule(hoaId, rule.id))} aria-label="Disable rule">
                              <Ban className="h-4 w-4" />
                            </button>
                            <button type="button" className="rounded border border-[#d4d4d4] p-1.5 hover:bg-[#f5f5f5]" onClick={() => startEdit(rule)} aria-label="Edit rule">
                              <Pencil className="h-4 w-4" />
                            </button>
                          </div>
                          {editingRuleId === rule.id && (
                            <div className="mt-2 flex min-w-[260px] gap-2">
                              <input
                                value={editLabel}
                                onChange={(event) => setEditLabel(event.target.value)}
                                className="min-w-0 flex-1 rounded border border-[#d4d4d4] px-2 py-1 text-sm"
                                aria-label="Rule label"
                              />
                              <button
                                type="button"
                                className="rounded border border-[#d4d4d4] p-1.5 hover:bg-[#f5f5f5]"
                                onClick={() => void runAction(`edit-${rule.id}`, () => editMappingRule(hoaId, rule.id, {
                                  pool_key: rule.pool_key,
                                  match_label: editLabel,
                                  match_type: rule.match_type,
                                })).then(() => setEditingRuleId(null))}
                                aria-label="Save rule"
                              >
                                <Save className="h-4 w-4" />
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="grid gap-6 lg:grid-cols-2">
              <div className="rounded-lg border border-[#e5e5e5] bg-white p-5">
                <h2 className="mb-4 text-lg font-semibold text-[#111111]">Aliases</h2>
                <div className="space-y-3">
                  {state.aliases.length === 0 ? (
                    <p className="text-sm text-[#737373]">No aliases approved.</p>
                  ) : state.aliases.map((alias) => (
                    <div key={alias.id} className="flex items-center justify-between rounded border border-[#eeeeee] px-3 py-2 text-sm">
                      <span>{alias.dre_label} → {alias.budget_label}</span>
                      <button type="button" className="rounded border border-[#d4d4d4] p-1.5 hover:bg-[#f5f5f5]" onClick={() => void runAction(`alias-${alias.id}`, () => revokeMappingAlias(hoaId, alias.id))} aria-label="Revoke alias">
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                  ))}
                  <div className="grid gap-2 border-t border-[#eeeeee] pt-3 sm:grid-cols-4">
                    <input value={aliasDraft.pool_key} onChange={(event) => setAliasDraft({ ...aliasDraft, pool_key: event.target.value })} placeholder="Pool key" aria-label="Alias pool key" className="rounded border border-[#d4d4d4] px-2 py-1 text-sm" />
                    <input value={aliasDraft.dre_label} onChange={(event) => setAliasDraft({ ...aliasDraft, dre_label: event.target.value })} placeholder="DRE label" aria-label="Alias DRE label" className="rounded border border-[#d4d4d4] px-2 py-1 text-sm" />
                    <input value={aliasDraft.budget_label} onChange={(event) => setAliasDraft({ ...aliasDraft, budget_label: event.target.value })} placeholder="Budget label" aria-label="Alias budget label" className="rounded border border-[#d4d4d4] px-2 py-1 text-sm" />
                    <button type="button" className="rounded-lg bg-[#111111] px-3 py-1.5 text-sm font-medium text-white" onClick={() => void runAction('alias-create', () => createMappingAlias(hoaId, aliasDraft))}>
                      Add
                    </button>
                  </div>
                </div>
              </div>

              <div className="rounded-lg border border-[#e5e5e5] bg-white p-5">
                <h2 className="mb-4 text-lg font-semibold text-[#111111]">Exemptions</h2>
                <div className="space-y-3">
                  {state.exemption_decisions.length === 0 ? (
                    <p className="text-sm text-[#737373]">No exemption decisions pending.</p>
                  ) : state.exemption_decisions.map((decision) => (
                    <div key={decision.pool_key} className="rounded border border-[#eeeeee] p-3 text-sm">
                      <div className="flex items-center justify-between gap-3">
                        <span className="font-medium text-[#111111]">{decision.pool_key}</span>
                        <StatusBadge value={decision.exemption_state} />
                      </div>
                      <input
                        value={exemptionNotes[decision.pool_key] || ''}
                        onChange={(event) => setExemptionNotes({ ...exemptionNotes, [decision.pool_key]: event.target.value })}
                        placeholder="Decision note"
                        aria-label={`${decision.pool_key} exemption decision note`}
                        className="mt-3 w-full rounded border border-[#d4d4d4] px-2 py-1 text-sm"
                      />
                      <div className="mt-3 flex gap-2">
                        {(['active', 'inactive', 'pending_review'] as const).map((nextState) => (
                          <button
                            key={nextState}
                            type="button"
                            className="rounded border border-[#d4d4d4] px-2 py-1 text-xs hover:bg-[#f5f5f5]"
                            onClick={() => void runAction(`exemption-${decision.pool_key}-${nextState}`, () => setExemptionDecision(hoaId, decision.pool_key, {
                              exemption_state: nextState,
                              budget_year: decision.budget_year || undefined,
                              note: exemptionNotes[decision.pool_key] || '',
                            }))}
                          >
                            {humanize(nextState)}
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </section>

            <section className="rounded-lg border border-[#e5e5e5] bg-white p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-[#111111]">Residual Preview</h2>
                  <p className="text-sm text-[#737373]">Diagnostic only. Assign residual/base rows from the review table.</p>
                </div>
                <button type="button" className="rounded-lg border border-[#d4d4d4] px-3 py-1.5 text-sm hover:bg-[#f5f5f5]" onClick={() => void runAction('residual', () => approveResidualRouting(hoaId))}>
                  Approve residual rule
                </button>
              </div>
              <div className="space-y-3 text-sm">
                <p className="font-medium text-[#111111]">Remaining candidate lines</p>
                {state.residual_preview.candidate_lines.length === 0 ? (
                  <p className="text-[#737373]">No candidate lines.</p>
                ) : state.residual_preview.candidate_lines.map((line, index) => (
                  <div key={`${line.line_label}-${index}`} className="flex justify-between rounded border border-[#eeeeee] px-3 py-2">
                    <span>{line.line_label}</span>
                    <span>{line.amount == null ? '-' : formatCurrency(line.amount)}</span>
                  </div>
                ))}
                <p className="pt-3 font-medium text-[#111111]">Excluded / unresolved preview rows</p>
                <p className="text-[#737373]">{state.residual_preview.excluded_lines.length} rows excluded from regular basis.</p>
              </div>
            </section>
              </div>
            </details>
          </>
        )}
      </main>
    </div>
  );
}
