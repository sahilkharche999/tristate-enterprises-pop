// Manual assessment setup entry form — for an HOA with no formal DRE or
// CC&R on file. Operator picks a setup type, then enters assessment categories and
// (depending on setup type) groups or units directly. Submitting creates
// a synthetic extraction run that flows into the same Review Workbench
// as any Gemini-derived run.

import { useState } from 'react';
import {
  createManualAssessmentSetup,
  type ManualGroupEntry,
  type ManualPoolEntry,
  type ManualUnitEntry,
  type PromptAllocationMethod,
  type PromptSetupType,
} from '../api/manualSetup';

type Props = {
  hoaId: number;
  onCreated: (runId: number) => void;
  onCancel: () => void;
};

const SETUP_TYPE_OPTIONS: Array<{ value: PromptSetupType; label: string }> = [
  { value: 'fixed_equal', label: 'Fixed — all units pay the same' },
  { value: 'grouped_category', label: 'Grouped — dues vary by category' },
  { value: 'individual_unit', label: 'Per-unit — each unit has its own value' },
];

const ALLOCATION_METHOD_OPTIONS: Array<{ value: PromptAllocationMethod; label: string }> = [
  { value: 'equal', label: 'Equal' },
  { value: 'square_footage', label: 'Square footage' },
  { value: 'ownership_percentage', label: 'Ownership percentage' },
  { value: 'category', label: 'Category' },
  { value: 'specified_value', label: 'Specified value (per unit)' },
  { value: 'parking_space', label: 'Parking space' },
  { value: 'custom_factor', label: 'Custom factor' },
];

const inputCls =
  'w-full rounded-md border border-[#d4d4d4] px-2 py-1.5 text-sm focus:border-[#a3a3a3] focus:outline-none';
const labelCls = 'block text-xs font-medium text-[#525252] mb-1';

function emptyPool(): ManualPoolEntry {
  return { pool_key: '', pool_name: '', allocation_method: 'equal', annual_amount: null };
}
function emptyGroup(): ManualGroupEntry {
  return { group_id: '', label: '', unit_count: 1 };
}
function emptyUnit(): ManualUnitEntry {
  return { unit_number: '' };
}

export function ManualSetupEntryForm({ hoaId, onCreated, onCancel }: Props) {
  const [setupType, setSetupType] = useState<PromptSetupType>('fixed_equal');
  const [pools, setPools] = useState<ManualPoolEntry[]>([emptyPool()]);
  const [groups, setGroups] = useState<ManualGroupEntry[]>([emptyGroup()]);
  const [units, setUnits] = useState<ManualUnitEntry[]>([emptyUnit()]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function updatePool(i: number, patch: Partial<ManualPoolEntry>) {
    setPools((cur) => cur.map((p, idx) => (idx === i ? { ...p, ...patch } : p)));
  }
  function updateGroup(i: number, patch: Partial<ManualGroupEntry>) {
    setGroups((cur) => cur.map((g, idx) => (idx === i ? { ...g, ...patch } : g)));
  }
  function updateUnit(i: number, patch: Partial<ManualUnitEntry>) {
    setUnits((cur) => cur.map((u, idx) => (idx === i ? { ...u, ...patch } : u)));
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    const cleanedPools = pools
      .filter((p) => p.pool_key.trim() !== '')
      .map((p) => ({ ...p, pool_key: p.pool_key.trim() }));
    if (cleanedPools.length === 0) {
      setError('Enter at least one assessment category with a category key.');
      return;
    }

    const cleanedGroups =
      setupType === 'grouped_category'
        ? groups.filter((g) => (g.label || g.group_id || '').trim() !== '')
        : [];
    const cleanedUnits =
      setupType === 'individual_unit'
        ? units.filter((u) => u.unit_number.trim() !== '')
        : [];

    setSubmitting(true);
    try {
      const resp = await createManualAssessmentSetup(hoaId, {
        setup_type: setupType,
        pools: cleanedPools,
        groups: cleanedGroups,
        units: cleanedUnits,
      });
      onCreated(resp.extraction_run_id);
    } catch (exc) {
      setError(exc && typeof exc === 'object' && 'message' in exc
        ? String((exc as { message?: unknown }).message)
        : String(exc));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-5 rounded-xl border border-[#e5e5e5] bg-white p-4 shadow-sm">
      <header className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-[#111111]">Manual assessment setup entry</h3>
          <p className="text-xs text-[#737373]">
            For an HOA with no formal DRE or governing document on file. Enter assessment categories and
            {setupType === 'grouped_category' ? ' groups' : setupType === 'individual_unit' ? ' units' : ''}
            {' '}directly — you'll review and approve this in the same Workbench as any other run.
          </p>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-[#d4d4d4] px-2 py-1 text-xs text-[#525252] hover:bg-[#f5f5f5]"
        >
          Cancel
        </button>
      </header>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <div>
        <label className={labelCls}>Setup type</label>
        <select
          className={inputCls}
          value={setupType}
          onChange={(e) => setSetupType(e.target.value as PromptSetupType)}
        >
          {SETUP_TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {/* ── Assessment categories ────────────────────────────────── */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className={labelCls}>Assessment categories</label>
          <button
            type="button"
            onClick={() => setPools((cur) => [...cur, emptyPool()])}
            className="rounded border border-[#d4d4d4] px-2 py-0.5 text-xs text-[#111111] hover:bg-[#f5f5f5]"
          >
            + Add category
          </button>
        </div>
        <div className="space-y-2">
          {pools.map((p, i) => (
            <div key={i} className="grid grid-cols-12 items-end gap-2 rounded border border-[#e5e5e5] p-2">
              <div className="col-span-3">
                <label className={labelCls}>Category key</label>
                <input
                  className={inputCls}
                  value={p.pool_key}
                  placeholder="operating"
                  onChange={(e) => updatePool(i, { pool_key: e.target.value })}
                />
              </div>
              <div className="col-span-3">
                <label className={labelCls}>Name</label>
                <input
                  className={inputCls}
                  value={p.pool_name || ''}
                  placeholder="Operating expenses"
                  onChange={(e) => updatePool(i, { pool_name: e.target.value })}
                />
              </div>
              <div className="col-span-3">
                <label className={labelCls}>Allocation method</label>
                <select
                  className={inputCls}
                  value={p.allocation_method}
                  onChange={(e) =>
                    updatePool(i, { allocation_method: e.target.value as PromptAllocationMethod })
                  }
                >
                  {ALLOCATION_METHOD_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="col-span-2">
                <label className={labelCls}>Annual amount</label>
                <input
                  type="number"
                  className={inputCls}
                  value={p.annual_amount ?? ''}
                  onChange={(e) =>
                    updatePool(i, {
                      annual_amount: e.target.value === '' ? null : Number(e.target.value),
                    })
                  }
                />
              </div>
              <div className="col-span-1 flex justify-end">
                <button
                  type="button"
                  disabled={pools.length === 1}
                  onClick={() => setPools((cur) => cur.filter((_, idx) => idx !== i))}
                  className="rounded border border-rose-200 px-2 py-1 text-xs text-rose-700 hover:bg-rose-50 disabled:opacity-40"
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Groups (grouped setup only) ──────────────────────────── */}
      {setupType === 'grouped_category' && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className={labelCls}>Groups</label>
            <button
              type="button"
              onClick={() => setGroups((cur) => [...cur, emptyGroup()])}
              className="rounded border border-[#d4d4d4] px-2 py-0.5 text-xs text-[#111111] hover:bg-[#f5f5f5]"
            >
              + Add group
            </button>
          </div>
          <div className="space-y-2">
            {groups.map((g, i) => (
              <div key={i} className="grid grid-cols-12 items-end gap-2 rounded border border-[#e5e5e5] p-2">
                <div className="col-span-3">
                  <label className={labelCls}>Label</label>
                  <input
                    className={inputCls}
                    value={g.label || ''}
                    placeholder="Residential"
                    onChange={(e) => updateGroup(i, { label: e.target.value })}
                  />
                </div>
                <div className="col-span-2">
                  <label className={labelCls}>Unit count</label>
                  <input
                    type="number"
                    className={inputCls}
                    value={g.unit_count}
                    onChange={(e) => updateGroup(i, { unit_count: Number(e.target.value) || 0 })}
                  />
                </div>
                <div className="col-span-2">
                  <label className={labelCls}>Avg sq ft</label>
                  <input
                    type="number"
                    className={inputCls}
                    value={g.average_square_feet ?? ''}
                    onChange={(e) =>
                      updateGroup(i, {
                        average_square_feet: e.target.value === '' ? null : Number(e.target.value),
                      })
                    }
                  />
                </div>
                <div className="col-span-2">
                  <label className={labelCls}>Ownership %</label>
                  <input
                    type="number"
                    className={inputCls}
                    value={g.ownership_percent ?? ''}
                    onChange={(e) =>
                      updateGroup(i, {
                        ownership_percent: e.target.value === '' ? null : Number(e.target.value),
                      })
                    }
                  />
                </div>
                <div className="col-span-3 flex justify-end">
                  <button
                    type="button"
                    disabled={groups.length === 1}
                    onClick={() => setGroups((cur) => cur.filter((_, idx) => idx !== i))}
                    className="rounded border border-rose-200 px-2 py-1 text-xs text-rose-700 hover:bg-rose-50 disabled:opacity-40"
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Units (per-unit setup only) ──────────────────────────── */}
      {setupType === 'individual_unit' && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className={labelCls}>Units</label>
            <button
              type="button"
              onClick={() => setUnits((cur) => [...cur, emptyUnit()])}
              className="rounded border border-[#d4d4d4] px-2 py-0.5 text-xs text-[#111111] hover:bg-[#f5f5f5]"
            >
              + Add unit
            </button>
          </div>
          <div className="space-y-2">
            {units.map((u, i) => (
              <div key={i} className="grid grid-cols-12 items-end gap-2 rounded border border-[#e5e5e5] p-2">
                <div className="col-span-2">
                  <label className={labelCls}>Unit #</label>
                  <input
                    className={inputCls}
                    value={u.unit_number}
                    placeholder="101"
                    onChange={(e) => updateUnit(i, { unit_number: e.target.value })}
                  />
                </div>
                <div className="col-span-2">
                  <label className={labelCls}>Sq ft</label>
                  <input
                    type="number"
                    className={inputCls}
                    value={u.square_feet ?? ''}
                    onChange={(e) =>
                      updateUnit(i, {
                        square_feet: e.target.value === '' ? null : Number(e.target.value),
                      })
                    }
                  />
                </div>
                <div className="col-span-2">
                  <label className={labelCls}>Ownership %</label>
                  <input
                    type="number"
                    className={inputCls}
                    value={u.ownership_percent ?? ''}
                    onChange={(e) =>
                      updateUnit(i, {
                        ownership_percent: e.target.value === '' ? null : Number(e.target.value),
                      })
                    }
                  />
                </div>
                <div className="col-span-2">
                  <label className={labelCls}>Category</label>
                  <input
                    className={inputCls}
                    value={u.category || ''}
                    placeholder="residential"
                    onChange={(e) => updateUnit(i, { category: e.target.value })}
                  />
                </div>
                <div className="col-span-2">
                  <label className={labelCls}>Parking spaces</label>
                  <input
                    type="number"
                    className={inputCls}
                    value={u.parking_spaces ?? ''}
                    onChange={(e) =>
                      updateUnit(i, {
                        parking_spaces: e.target.value === '' ? undefined : Number(e.target.value),
                      })
                    }
                  />
                </div>
                <div className="col-span-2 flex justify-end">
                  <button
                    type="button"
                    disabled={units.length === 1}
                    onClick={() => setUnits((cur) => cur.filter((_, idx) => idx !== i))}
                    className="rounded border border-rose-200 px-2 py-1 text-xs text-rose-700 hover:bg-rose-50 disabled:opacity-40"
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-[#d4d4d4] px-3 py-2 text-sm text-[#111111] hover:bg-[#f5f5f5]"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={submitting}
          className="rounded-md bg-[#111111] px-3 py-2 text-sm text-white hover:bg-[#262626] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? 'Creating…' : 'Create & review'}
        </button>
      </div>
    </form>
  );
}
