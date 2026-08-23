import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router';

import {
  approveAllocationResolution,
  draftAllocationResolution,
  getAllocationPreview,
  getAllocationResolution,
  saveAllocationSlices,
  saveCategoryDecision,
  type AllocationResolutionState,
} from '../api/allocationResolution';
import { getErrorMessage } from '../lib/errors';
import { combinedLineHint, issueAnchor, slicesBalance } from '../lib/allocationResolution';

type Props = {
  hoaId: number;
};

export function AllocationResolutionPanel({ hoaId }: Props) {
  const [state, setState] = useState<AllocationResolutionState | null>(null);
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [methodByPool, setMethodByPool] = useState<Record<string, string>>({});
  const [splitLabel, setSplitLabel] = useState('');
  const [splitAmount, setSplitAmount] = useState('');
  const [splitLeft, setSplitLeft] = useState('');
  const [splitRight, setSplitRight] = useState('');
  const [leftPool, setLeftPool] = useState('');
  const [rightPool, setRightPool] = useState('');
  const [leftCategory, setLeftCategory] = useState('');
  const [rightCategory, setRightCategory] = useState('');
  const [hintApplied, setHintApplied] = useState(false);

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

  const poolKeys = useMemo(
    () => (state?.resolutions ?? []).map((row) => String(row.pool_key)).filter(Boolean),
    [state],
  );

  const unresolvedPools = useMemo(
    () => (state?.resolutions ?? []).filter((row) => {
      const declared = String(row.declared_method || '');
      const status = String(row.status || '');
      return ['custom_factor', 'external_schedule', 'unknown'].includes(declared) && status !== 'approved';
    }),
    [state],
  );

  useEffect(() => {
    if (!state || hintApplied) return;
    const hint = combinedLineHint(state.readiness.issues);
    if (!hint) return;
    setSplitLabel((current) => current || hint.lineLabel);
    setLeftCategory((current) => current || hint.category);
    setLeftPool((current) => current || hint.poolKey);
    const residual = poolKeys.find((key) => key !== hint.poolKey) || '';
    setRightPool((current) => current || residual);
    setHintApplied(true);
  }, [hintApplied, poolKeys, state]);

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

  const splitDelta = slicesBalance(Number(splitAmount || 0), [
    { pool_key: leftPool, semantic_category: leftCategory, slice_annual_amount: splitLeft },
    { pool_key: rightPool, semantic_category: rightCategory, slice_annual_amount: splitRight },
  ]);
  const canSaveSplit = Boolean(splitLabel && splitAmount && leftPool && rightPool && leftCategory && splitLeft && splitRight)
    && splitDelta === 0
    && busy === null;

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
            categories, slices, factors, and approval reconcile.
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
          No allocation-resolution records for this setup yet. Re-promote the governing document, or run
          the migration report, to create them from the extracted rules.
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {state.resolutions.map((row) => {
          const poolKey = String(row.pool_key);
          const declared = String(row.declared_method);
          const selected = methodByPool[poolKey] || String(row.resolved_method || '');
          return (
            <article
              key={poolKey}
              id={issueAnchor(`pool:${poolKey}`)}
              className="rounded-lg border border-[#eeeeee] p-4"
            >
              <h3 className="font-medium text-[#111111]">{poolKey}</h3>
              <p className="mt-1 text-sm text-[#525252]">
                Declared <strong>{declared}</strong>
                {row.declared_denominator_label ? ` — ${String(row.declared_denominator_label)}` : ''}
              </p>
              <p className="mt-1 text-xs text-[#737373]">
                Status {String(row.status)} · source pages {JSON.stringify((row.evidence as { source_pages?: number[] })?.source_pages || [])}
              </p>
              {['custom_factor', 'external_schedule', 'unknown'].includes(declared) && (
                <div className="mt-3 space-y-2">
                  <label className="block text-xs font-medium text-[#525252]">
                    Executable basis
                    <select
                      className="mt-1 w-full rounded-md border border-[#d4d4d4] px-2 py-1.5 text-sm"
                      value={selected}
                      onChange={(event) => setMethodByPool((prev) => ({ ...prev, [poolKey]: event.target.value }))}
                    >
                      <option value="">Select…</option>
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
        <h3 className="font-medium text-[#111111]">Combined-line splitter</h3>
        <p className="mt-1 text-sm text-[#525252]">
          Use this when one budget line covers more than one declared category. The source amount stays
          immutable; the slices must sum to it. Fields start empty and only prefill from a readiness
          combined-line issue for this setup.
        </p>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <label className="text-xs text-[#525252]">
            Source line
            <input
              className="mt-1 w-full rounded-md border px-2 py-1.5 text-sm"
              placeholder="Budget line label"
              value={splitLabel}
              onChange={(e) => setSplitLabel(e.target.value)}
            />
          </label>
          <label className="text-xs text-[#525252]">
            Source annual
            <input
              className="mt-1 w-full rounded-md border px-2 py-1.5 text-sm"
              placeholder="0.00"
              value={splitAmount}
              onChange={(e) => setSplitAmount(e.target.value)}
            />
          </label>
          <label className="text-xs text-[#525252]">
            Slice 1 category
            <input className="mt-1 w-full rounded-md border px-2 py-1.5 text-sm" placeholder="Category from the governing document" value={leftCategory} onChange={(e) => setLeftCategory(e.target.value)} />
          </label>
          <label className="text-xs text-[#525252]">
            Slice 1 amount
            <input className="mt-1 w-full rounded-md border px-2 py-1.5 text-sm" placeholder="0.00" value={splitLeft} onChange={(e) => setSplitLeft(e.target.value)} />
          </label>
          <label className="text-xs text-[#525252]">
            Slice 1 pool
            <select className="mt-1 w-full rounded-md border px-2 py-1.5 text-sm" value={leftPool} onChange={(e) => setLeftPool(e.target.value)}>
              <option value="">Select pool…</option>
              {poolKeys.map((key) => <option key={key} value={key}>{key}</option>)}
            </select>
          </label>
          <label className="text-xs text-[#525252]">
            Slice 2 category
            <input className="mt-1 w-full rounded-md border px-2 py-1.5 text-sm" placeholder="Remaining category" value={rightCategory} onChange={(e) => setRightCategory(e.target.value)} />
          </label>
          <label className="text-xs text-[#525252]">
            Slice 2 amount
            <input className="mt-1 w-full rounded-md border px-2 py-1.5 text-sm" placeholder="0.00" value={splitRight} onChange={(e) => setSplitRight(e.target.value)} />
          </label>
          <label className="text-xs text-[#525252]">
            Slice 2 pool
            <select className="mt-1 w-full rounded-md border px-2 py-1.5 text-sm" value={rightPool} onChange={(e) => setRightPool(e.target.value)}>
              <option value="">Select pool…</option>
              {poolKeys.map((key) => <option key={key} value={key}>{key}</option>)}
            </select>
          </label>
        </div>
        <p className="mt-2 text-xs text-[#737373]">
          {!splitAmount
            ? 'Enter the source amount and both slices.'
            : splitDelta === 0
              ? 'Slices balance the source line.'
              : `Slices are off by ${splitDelta}.`}
        </p>
        <button
          type="button"
          className="mt-2 rounded-md border border-[#d4d4d4] px-3 py-1.5 text-sm"
          disabled={!canSaveSplit}
          onClick={async () => {
            setBusy('slices');
            try {
              await saveAllocationSlices(hoaId, {
                source_line_label: splitLabel,
                source_annual_amount: splitAmount,
                slices: [
                  { pool_key: leftPool, semantic_category: leftCategory, slice_annual_amount: splitLeft },
                  { pool_key: rightPool, semantic_category: rightCategory, slice_annual_amount: splitRight },
                ],
              });
              await load();
            } catch (err) {
              setError(getErrorMessage(err, 'Could not save slices.'));
            } finally {
              setBusy(null);
            }
          }}
        >
          Save split
        </button>
      </div>

      <div className="rounded-lg border border-[#eeeeee] p-4">
        <h3 className="font-medium text-[#111111]">Required categories</h3>
        {unresolvedPools.length === 0 ? (
          <p className="mt-2 text-sm text-[#666666]">No unresolved exception categories on this setup.</p>
        ) : (
          <div className="mt-3 space-y-2">
            {unresolvedPools.flatMap((row) => (
              ((row.included_categories as string[]) || []).map((category) => (
                <div key={`${row.pool_key}-${category}`} id={issueAnchor(`category:${row.pool_key}:${category}`)} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span>{String(row.pool_key)} · {category}</span>
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
