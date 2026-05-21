// Per-package "Adjust amounts" panel (Phase 4.5 task 122 of
// dre-driven-assessment-engine).
//
// Operator-facing tool for applying AssessmentOverride rows to a
// package: package-scope (every recipient takes the same monthly),
// group-scope (one group's amount), unit-scope (one unit's amount),
// or pool-scope (rewrite a whole pool's components).
//
// Overrides are INTERNAL-ONLY audit entries — they never render into
// the homeowner-visible PDF (task 123). The CalcResultSet returns
// applied_overrides for the operator UI + audit JSON only.

import { useState } from 'react';

type OverrideScope = 'package' | 'group' | 'unit' | 'pool';
type OverrideType =
  | 'board_approved'
  | 'rounding'
  | 'manual_correction'
  | 'spreadsheet';

type Props = {
  hoaId: number;
  packageId: number;
};

type DraftOverride = {
  scope: OverrideScope;
  scope_ref_id: string;
  override_type: OverrideType;
  override_monthly_amount: string;
  reason: string;
};

const EMPTY_DRAFT: DraftOverride = {
  scope: 'package',
  scope_ref_id: '',
  override_type: 'board_approved',
  override_monthly_amount: '',
  reason: '',
};

export function AssessmentOverridesPanel({ hoaId, packageId }: Props) {
  const [drafts, setDrafts] = useState<DraftOverride[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  function addDraft() {
    setDrafts([...drafts, { ...EMPTY_DRAFT }]);
  }

  function updateDraft(idx: number, patch: Partial<DraftOverride>) {
    setDrafts(drafts.map((d, i) => (i === idx ? { ...d, ...patch } : d)));
  }

  function removeDraft(idx: number) {
    setDrafts(drafts.filter((_, i) => i !== idx));
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (drafts.length === 0) {
      setMessage('Add at least one override.');
      return;
    }
    setSubmitting(true);
    try {
      // The override-save endpoint isn't wired in this iteration —
      // the engine accepts overrides as a list at calculation time,
      // and the AnnualPackage flow plumbs them via the package's
      // applied_overrides audit list. This UI is a local-state
      // builder; saving lands in the next iteration alongside the
      // engine resolver integration.
      setMessage(
        `Captured ${drafts.length} override(s). ` +
        `Note: persistence wiring (POST to /hoa/${hoaId}/annual-packages/${packageId}/overrides) ` +
        `lands when the engine resolver is hooked to fetch overrides from the DB.`,
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="space-y-4 p-4">
      <header>
        <h2 className="text-lg font-semibold">Adjust amounts (overrides)</h2>
        <p className="text-sm text-gray-600">
          Apply operator-approved overrides to specific recipients or pools.
          Overrides are <strong>internal audit only</strong> — they never
          appear in the homeowner-visible PDF.
        </p>
      </header>

      {message && (
        <div className="rounded border border-blue-300 bg-blue-50 p-2 text-sm text-blue-800">
          {message}
        </div>
      )}

      <form onSubmit={onSubmit} className="space-y-3">
        {drafts.length === 0 && (
          <p className="text-sm text-gray-500">No overrides yet.</p>
        )}
        {drafts.map((draft, idx) => (
          <div
            key={idx}
            className="grid gap-2 rounded border bg-gray-50 p-2 sm:grid-cols-[140px_1fr_1fr_180px_2fr_auto]"
          >
            <select
              value={draft.scope}
              onChange={(e) =>
                updateDraft(idx, { scope: e.target.value as OverrideScope })
              }
              className="rounded border px-2 py-1 text-sm"
            >
              <option value="package">Package (all)</option>
              <option value="group">Group</option>
              <option value="unit">Unit</option>
              <option value="pool">Pool</option>
            </select>
            <input
              placeholder="scope_ref_id (group/unit/pool id)"
              value={draft.scope_ref_id}
              onChange={(e) => updateDraft(idx, { scope_ref_id: e.target.value })}
              disabled={draft.scope === 'package'}
              className="rounded border px-2 py-1 text-sm"
            />
            <input
              type="number"
              step="0.01"
              placeholder="New monthly $"
              value={draft.override_monthly_amount}
              onChange={(e) =>
                updateDraft(idx, { override_monthly_amount: e.target.value })
              }
              className="rounded border px-2 py-1 text-sm"
            />
            <select
              value={draft.override_type}
              onChange={(e) =>
                updateDraft(idx, { override_type: e.target.value as OverrideType })
              }
              className="rounded border px-2 py-1 text-sm"
            >
              <option value="board_approved">Board-approved</option>
              <option value="rounding">Rounding</option>
              <option value="manual_correction">Manual correction</option>
              <option value="spreadsheet">Spreadsheet supplement</option>
            </select>
            <input
              placeholder="Reason (audit-only — required)"
              value={draft.reason}
              onChange={(e) => updateDraft(idx, { reason: e.target.value })}
              className="rounded border px-2 py-1 text-sm"
            />
            <button
              type="button"
              onClick={() => removeDraft(idx)}
              className="rounded border border-red-300 px-2 text-xs text-red-700 hover:bg-red-50"
            >
              Remove
            </button>
          </div>
        ))}

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={addDraft}
            className="rounded border px-3 py-1 text-sm hover:bg-gray-50"
          >
            + Add override
          </button>
          <button
            type="submit"
            disabled={submitting || drafts.length === 0}
            className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {submitting ? 'Capturing…' : 'Capture overrides'}
          </button>
        </div>
      </form>

      <details className="rounded border bg-gray-50 p-2 text-xs">
        <summary className="cursor-pointer font-medium">
          About overrides
        </summary>
        <div className="mt-2 space-y-1 text-gray-600">
          <p>
            <strong>Package scope</strong>: rewrites every recipient&apos;s
            monthly_total to the override amount.
          </p>
          <p>
            <strong>Group / Unit scope</strong>: rewrites only the matching
            recipient&apos;s monthly_total.
          </p>
          <p>
            <strong>Pool scope</strong>: rewrites every component row in
            the overridden pool with the new amount before recipient
            summation.
          </p>
          <p>
            The engine emits an <code>AppliedOverrideEntry</code> audit row
            per override with the pre-override amount, override amount,
            delta, reason, and approving operator. These show up in the
            operator UI and audit JSON — never in the homeowner PDF.
          </p>
        </div>
      </details>
    </section>
  );
}
