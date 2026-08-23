import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router';

import {
  approveAllocationResolution,
  draftAllocationResolution,
  getAllocationPreview,
  getAllocationResolution,
  saveCategoryDecision,
  type AllocationResolutionState,
} from '../api/allocationResolution';
import { getErrorMessage } from '../lib/errors';
import { issueAnchor } from '../lib/allocationResolution';

type Props = {
  hoaId: number;
};

function assessmentCategoryName(
  categories: AllocationResolutionState['assessment_categories'],
  key: string | null | undefined,
) {
  if (!key) return '';
  return categories.find((category) => category.pool_key === key)?.pool_name || humanize(key);
}

export function AllocationResolutionPanel({ hoaId }: Props) {
  const [state, setState] = useState<AllocationResolutionState | null>(null);
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [methodByPool, setMethodByPool] = useState<Record<string, string>>({});

  async function load() {
    setBusy('load');
    setError(null);
    try {
      const next = await getAllocationResolution(hoaId);
      setState(next);
      if (next.readiness.preview_available) {
        setPreview(await getAllocationPreview(hoaId));
      } else {
        setPreview(null);
      }
    } catch (err) {
      setError(getErrorMessage(err, 'Allocation resolution is not available yet.'));
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    void load();
  }, [hoaId]);

  const unresolvedPools = useMemo(
    () => (state?.resolutions ?? []).filter((row) => {
      const declared = String(row.declared_method || '');
      const status = String(row.status || '');
      return ['custom_factor', 'external_schedule', 'unknown', 'category'].includes(declared)
        && status !== 'approved';
    }),
    [state],
  );

  if (error && !state) {
    return null;
  }
  if (!state) {
    return (
      <section className="rounded-lg border border-[#e5e5e5] bg-white p-5 text-sm text-[#666666]">
        {busy ? 'Loading allocation resolution…' : null}
      </section>
    );
  }

  const monthlyByUnit = ((preview as { preview?: { monthly_by_unit?: Record<string, string> } } | null)
    ?.preview?.monthly_by_unit) || {};
  const hasPreviewDollars = Object.values(monthlyByUnit).some((value) => Number(value) !== 0);

  return (
    <section id="allocation-resolution" className="space-y-4 rounded-lg border border-[#e5e5e5] bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-[#111111]">Allocation resolution</h2>
          <p className="mt-1 text-sm text-[#525252]">
            Keep the governing-document rule, then choose an executable method. Final PDFs stay blocked until
            assessment categories, factors, and approval reconcile.
          </p>
        </div>
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
          state.blocks_final
            ? 'bg-amber-50 text-amber-800 ring-amber-600/20'
            : 'bg-emerald-50 text-emerald-700 ring-emerald-600/20'
        }`}>
          {state.blocks_final ? 'Final generation blocked' : 'Ready for final'}
        </span>
      </div>

      {state.blocks_final && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
          <p className="font-medium">Resolve allocation issues</p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {state.readiness.issues.map((issue) => (
              <li key={`${issue.code}-${issue.target}`}>
                <a className="underline" href={`#${issueAnchor(issue.target)}`}>{issue.message}</a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {state.resolutions.length === 0 && (
        <p className="text-sm text-[#525252]">
          {state.assessment_categories.length === 0
            ? 'This setup has no available assessment categories. Complete the governing-document setup and mapping step before assigning budget costs.'
            : 'This setup has no governing-document rules that need a separate decision. Use the mapping table below to assign budget costs to assessment categories.'}
          {state.assessment_categories.length === 0 && (
            <Link className="ml-1 underline" to={`/hoa/${hoaId}/settings?section=dre`}>
              Open setup and mapping
            </Link>
          )}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {state.resolutions.map((row) => {
          const poolKey = String(row.pool_key);
          const categoryName = assessmentCategoryName(state.assessment_categories, poolKey);
          const declared = String(row.declared_method);
          const selected = methodByPool[poolKey] || String(row.resolved_method || '');
          return (
            <article
              key={poolKey}
              id={issueAnchor(`pool:${poolKey}`)}
              className="rounded-lg border border-[#eeeeee] p-4"
            >
              <h3 className="font-medium text-[#111111]">Assessment category rule: {categoryName}</h3>
              <p className="mt-1 text-sm text-[#525252]">
                Governing document says <strong>{humanize(declared)}</strong>
                {row.declared_denominator_label ? ` — ${String(row.declared_denominator_label)}` : ''}
              </p>
              <p className="mt-1 text-xs text-[#737373]">
                Decision status: {humanize(String(row.status))} · source pages {JSON.stringify((row.evidence as { source_pages?: number[] })?.source_pages || [])}
              </p>
              {['custom_factor', 'external_schedule', 'unknown', 'category'].includes(declared) && (
                <div className="mt-3 space-y-2">
                  <label className="block text-xs font-medium text-[#525252]">
                    How this category is shared
                    <select
                      className="mt-1 w-full rounded-md border border-[#d4d4d4] px-2 py-1.5 text-sm"
                      value={selected}
                      onChange={(event) => setMethodByPool((prev) => ({ ...prev, [poolKey]: event.target.value }))}
                    >
                      <option value="">Choose a sharing method</option>
                      <option value="ownership_percentage">Use ownership percentage (confirm)</option>
                      <option value="square_footage">Use square footage (confirm)</option>
                      <option value="specified_value">Enter custom / specified factors</option>
                      <option value="equal">Equal share (confirm)</option>
                    </select>
                  </label>
                  <textarea
                    className="w-full rounded-md border border-[#d4d4d4] px-2 py-1.5 text-sm"
                    placeholder="Reason for this choice"
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    rows={2}
                  />
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="rounded-md border border-[#d4d4d4] px-3 py-1.5 text-sm"
                      disabled={!selected || !reason.trim() || busy !== null}
                      onClick={async () => {
                        setBusy(`draft-${poolKey}`);
                        try {
                          await draftAllocationResolution(hoaId, poolKey, {
                            resolved_method: selected,
                            confirmation: `I confirm ${selected}`,
                            reason,
                            prior_package_id: state.approved_schedules[0]?.id,
                          });
                          await load();
                        } catch (err) {
                          setError(getErrorMessage(err, 'Could not save draft resolution.'));
                        } finally {
                          setBusy(null);
                        }
                      }}
                    >
                      Save draft
                    </button>
                    <button
                      type="button"
                      className="rounded-md bg-[#111111] px-3 py-1.5 text-sm text-white"
                      disabled={!selected || !reason.trim() || busy !== null}
                      onClick={async () => {
                        setBusy(`approve-${poolKey}`);
                        try {
                          await approveAllocationResolution(hoaId, poolKey, {
                            resolved_method: selected,
                            confirmation: `I confirm ${selected} matches the referenced schedule`,
                            reason,
                            prior_package_id: state.approved_schedules[0]?.id,
                          });
                          await load();
                        } catch (err) {
                          setError(getErrorMessage(err, 'Could not approve resolution.'));
                        } finally {
                          setBusy(null);
                        }
                      }}
                    >
                      Approve method
                    </button>
                  </div>
                </div>
              )}
            </article>
          );
        })}
      </div>

      <div className="rounded-lg border border-[#eeeeee] p-4">
        <h3 className="font-medium text-[#111111]">Required assessment categories</h3>
        {unresolvedPools.length === 0 ? (
          <p className="mt-2 text-sm text-[#666666]">No unresolved exception categories on this setup.</p>
        ) : (
          <div className="mt-3 space-y-2">
            {unresolvedPools.flatMap((row) => (
              ((row.included_categories as string[]) || []).map((category) => (
                <div key={`${row.pool_key}-${category}`} id={issueAnchor(`category:${row.pool_key}:${category}`)} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span>
                    {assessmentCategoryName(state.assessment_categories, String(row.pool_key))} · {category}
                  </span>
                  <div className="flex gap-2">
                    {(['mapped', 'zero', 'not_applicable'] as const).map((decision) => (
                      <button
                        key={decision}
                        type="button"
                        className="rounded border border-[#d4d4d4] px-2 py-1 text-xs"
                        onClick={async () => {
                          await saveCategoryDecision(hoaId, {
                            pool_key: row.pool_key,
                            category,
                            decision,
                            reason: `${decision} for ${category}`,
                            evidence_text: String(row.declared_denominator_label || ''),
                          });
                          await load();
                        }}
                      >
                        {decision}
                      </button>
                    ))}
                  </div>
                </div>
              ))
            ))}
          </div>
        )}
      </div>

      <div className="rounded-lg border border-dashed border-[#d4d4d4] bg-[#fafafa] p-4">
        <h3 className="font-medium text-[#111111]">Non-final allocation preview</h3>
        <p className="mt-1 text-xs uppercase tracking-wide text-[#737373]">Preview only — not a final package</p>
        {hasPreviewDollars ? (
          <table className="mt-3 w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-[#737373]">
                <th className="py-1">Unit</th>
                <th className="py-1">Proposed monthly</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(monthlyByUnit).map(([unit, amount]) => (
                <tr key={unit}>
                  <td className="py-0.5">{unit}</td>
                  <td className="py-0.5">{amount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="mt-2 text-sm text-[#666666]">
            Preview amounts appear after a draft method and mapped dollars exist. Unit rows come from this
            HOA’s assessment setup, not from a hardcoded list.
          </p>
        )}
      </div>

      {state.blocks_final && (
        <Link
          to={`/hoa/${hoaId}/assessment-mapping-review#allocation-resolution`}
          className="inline-flex rounded-md bg-amber-700 px-4 py-2 text-sm font-medium text-white"
        >
          Resolve allocation issues
        </Link>
      )}
    </section>
  );
}
