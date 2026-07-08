// AnnualPackage list + create panel (Phase 4.8 of dre-driven-assessment-engine).
// Surfaces the per-HOA AnnualPackage lifecycle: list every package, create
// new draft, approve with revenue target, finalize (freezes snapshots).

import { useCallback, useEffect, useState } from 'react';
import {
  type AnnualPackage,
  approveAnnualPackage,
  createAnnualPackage,
  finalizeAnnualPackage,
  listAnnualPackages,
} from '../api/annualPackages';
import {
  assessmentModeLabel,
  assessmentModeWorkflowCopy,
  type AssessmentMode,
} from '../lib/assessmentMode';

type Props = {
  hoaId: number;
  liveAssessmentMode: AssessmentMode;
};

const STATUS_COLORS: Record<AnnualPackage['status'], string> = {
  draft: 'bg-gray-100 text-gray-700',
  preflight_failed: 'bg-yellow-100 text-yellow-800',
  approved: 'bg-blue-100 text-blue-800',
  rendered: 'bg-indigo-100 text-indigo-800',
  finalized: 'bg-green-100 text-green-800',
};

export function AnnualPackagesPanel({ hoaId, liveAssessmentMode }: Props) {
  const [packages, setPackages] = useState<AnnualPackage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newYear, setNewYear] = useState<string>(
    String(new Date().getFullYear() + 1),
  );

  const refresh = useCallback(async () => {
    try {
      const list = await listAnnualPackages(hoaId);
      setPackages(list);
      setError(null);
    } catch (exc) {
      setError(String(exc));
    } finally {
      setLoading(false);
    }
  }, [hoaId]);

  useEffect(() => {
    setLoading(true);
    refresh();
  }, [refresh]);

  async function onCreate(event: React.FormEvent) {
    event.preventDefault();
    const year = parseInt(newYear, 10);
    if (!Number.isFinite(year) || year < 2000 || year > 2100) {
      setError(`Invalid fiscal year: ${newYear}`);
      return;
    }
    try {
      await createAnnualPackage(hoaId, {
        budget_year: year,
        fiscal_year: year,
      });
      refresh();
    } catch (exc) {
      setError(String(exc));
    }
  }

  async function onCreateRegeneration(pkg: AnnualPackage) {
    try {
      await createAnnualPackage(hoaId, {
        budget_year: pkg.budget_year,
        fiscal_year: pkg.fiscal_year,
        regen_of_package_id: pkg.package_id,
      });
      refresh();
    } catch (exc) {
      setError(String(exc));
    }
  }

  async function onApprove(pkg: AnnualPackage) {
    const target = prompt(
      `Operator-approved annual assessment revenue for fiscal ${pkg.fiscal_year}?`,
      pkg.approved_assessment_revenue_annual ?? '',
    );
    if (target == null || !target.trim()) return;
    // Strip currency symbols, commas, and whitespace before sending —
    // Pydantic Decimal rejects "$75,468" / "75,468" with 422.
    const normalized = target.replace(/[\s$,]/g, '');
    if (!/^-?\d+(\.\d+)?$/.test(normalized)) {
      setError(
        `"${target}" is not a valid number. Enter a numeric amount (e.g. 75468 or 75468.50).`,
      );
      return;
    }
    try {
      await approveAnnualPackage(
        hoaId,
        pkg.package_id,
        { approved_assessment_revenue_annual: normalized },
        pkg.version_int,
      );
      refresh();
    } catch (exc) {
      setError(String(exc));
    }
  }

  async function onFinalize(pkg: AnnualPackage) {
    if (!confirm(
      `Finalize package for fiscal ${pkg.fiscal_year}? ` +
      `The server freezes the budget, reserve, assessment, appendix, and ` +
      `compile-context snapshots from current data, and the package becomes immutable.`,
    )) {
      return;
    }
    try {
      // C2: snapshot content is assembled SERVER-SIDE from canonical DB
      // state — the client sends nothing. A blocking preflight failure
      // (stale reserve study, unresolved placeholders, …) returns 422.
      await finalizeAnnualPackage(hoaId, pkg.package_id, pkg.version_int);
      refresh();
    } catch (exc) {
      setError(String(exc));
    }
  }

  if (loading) return <div className="p-4 text-gray-500">Loading packages…</div>;

  return (
    <section className="space-y-4 p-4">
      <header className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Annual disclosure packages</h2>
      </header>
      <div className="rounded border border-gray-200 bg-gray-50 p-3 text-xs text-gray-700">
        <p>
          <strong>Current assessment mode:</strong>{' '}
          {packages[0] ? assessmentModeLabel(packages[0].live_assessment_mode) : assessmentModeLabel(liveAssessmentMode)}
        </p>
        <p className="mt-2">
          {packages[0]
            ? assessmentModeWorkflowCopy(packages[0].live_assessment_mode)
            : assessmentModeWorkflowCopy(liveAssessmentMode)}
        </p>
      </div>

      {error && (
        <div className="rounded border border-red-300 bg-red-50 p-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th className="py-1">ID</th>
            <th>Fiscal year</th>
            <th>Status</th>
            <th>Approved revenue</th>
            <th>Approved by</th>
            <th>Finalized</th>
            <th>Version</th>
            <th>Assessment mode</th>
            <th>Impact</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {packages.length === 0 && (
            <tr>
              <td colSpan={10} className="py-4 text-center text-gray-500">
                No packages yet. Create one below.
              </td>
            </tr>
          )}
          {packages.map((pkg) => (
            <tr key={pkg.package_id} className="border-b">
              <td className="py-1">{pkg.package_id}</td>
              <td>{pkg.fiscal_year}</td>
              <td>
                <span
                  className={`inline-block rounded px-2 py-0.5 text-xs ${STATUS_COLORS[pkg.status]}`}
                >
                  {pkg.status}
                </span>
              </td>
              <td className="text-right tabular-nums">
                {pkg.approved_assessment_revenue_annual
                  ? `$${pkg.approved_assessment_revenue_annual}`
                  : '—'}
              </td>
              <td className="text-gray-600">{pkg.approved_by || '—'}</td>
              <td className="text-gray-600">
                {pkg.finalized_at ? pkg.finalized_at.slice(0, 10) : '—'}
              </td>
              <td className="text-gray-600">{pkg.version_int}</td>
              <td className="text-gray-600">
                <div>{assessmentModeLabel(pkg.assessment_mode)}</div>
                <div className="mt-1 text-xs text-gray-500">
                  Live: {assessmentModeLabel(pkg.live_assessment_mode)}
                </div>
              </td>
              <td className="text-gray-600">
                {pkg.package_impact === 'none' ? (
                  '—'
                ) : (
                  <div>
                    <div className="font-medium text-amber-700">
                      {pkg.package_impact === 'recheck_required' ? 'Recheck required' : 'Regeneration required'}
                    </div>
                    {pkg.package_impact_reason ? (
                      <div className="mt-1 max-w-xs text-xs text-amber-700">{pkg.package_impact_reason}</div>
                    ) : null}
                  </div>
                )}
              </td>
              <td className="space-x-1">
                {(pkg.status === 'draft' || pkg.status === 'preflight_failed') && (
                  <button
                    type="button"
                    onClick={() => onApprove(pkg)}
                    className="rounded border border-blue-400 px-2 py-0.5 text-xs text-blue-700 hover:bg-blue-50"
                  >
                    Approve
                  </button>
                )}
                {(pkg.status === 'approved' || pkg.status === 'rendered') && pkg.package_impact !== 'recheck_required' && (
                  <button
                    type="button"
                    onClick={() => onFinalize(pkg)}
                    className="rounded border border-green-400 px-2 py-0.5 text-xs text-green-700 hover:bg-green-50"
                  >
                    Finalize
                  </button>
                )}
                {pkg.package_impact === 'regeneration_required' && (
                  <button
                    type="button"
                    onClick={() => void onCreateRegeneration(pkg)}
                    className="rounded border border-amber-400 px-2 py-0.5 text-xs text-amber-700 hover:bg-amber-50"
                  >
                    Create Regeneration Draft
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <form
        onSubmit={onCreate}
        className="flex items-center gap-3 rounded border bg-gray-50 p-3"
      >
        <label className="text-sm">
          New package fiscal year:
          <input
            className="ml-2 rounded border px-2 py-1"
            value={newYear}
            onChange={(e) => setNewYear(e.target.value)}
            placeholder="2026"
          />
        </label>
        <button
          type="submit"
          className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700"
        >
          Create draft
        </button>
      </form>
    </section>
  );
}
