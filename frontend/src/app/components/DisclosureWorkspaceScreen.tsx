import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import {
  ArrowLeft,
  CheckCircle2,
  ClipboardCheck,
  FileArchive,
  FileText,
  Landmark,
  PackageCheck,
  PenLine,
  Settings,
  TableProperties,
} from 'lucide-react';

import { AnnualPackagesPanel } from './AnnualPackagesPanel';
import { BoilerplateWorkbench } from './BoilerplateWorkbench';
import { DisclosurePackagePanel } from './disclosure/DisclosurePackagePanel';
import { getHOA, type HOARecord } from '../api/hoa';
import { getErrorMessage } from '../lib/errors';
import {
  assessmentModeLabel,
  assessmentModeWorkflowCopy,
  assessmentModeShortLabel,
  type AssessmentMode,
} from '../lib/assessmentMode';

type ReadinessRow = {
  label: string;
  detail: string;
  href?: string;
  icon: typeof TableProperties;
  status: 'Ready' | 'Review setup';
  /** Opens full-screen package language workbench (not a navigation link). */
  action?: 'open-package-language';
};

function buildReadinessRows(hoaId: string, assessmentMode: AssessmentMode): ReadinessRow[] {
  return [
    {
      label: 'Budget draft',
      detail: 'Confirm source budget lines and generated draft before package generation.',
      href: `/hoa/${hoaId}?view=enriched`,
      icon: TableProperties,
      status: 'Review setup',
    },
    {
      label: 'Reserve study',
      detail: 'Review reserve rows, warnings, and funding-plan inputs.',
      href: `/hoa/${hoaId}?view=reserve`,
      icon: ClipboardCheck,
      status: 'Review setup',
    },
    {
      label: 'HOA settings',
      detail: 'Verify association identity, fiscal year, unit count, and disclosure defaults.',
      href: `/hoa/${hoaId}/settings?section=disclosure&returnTo=/hoa/${hoaId}/disclosure`,
      icon: Settings,
      status: 'Review setup',
    },
    {
      label: 'Package language',
      detail:
        'Open full-screen workbench to edit cover-letter intro and compare with a prior package PDF.',
      icon: PenLine,
      status: 'Review setup',
      action: 'open-package-language',
    },
    {
      label: 'DRE or assessment setup',
      detail:
        assessmentMode === 'fixed'
          ? 'Fixed mode is active. Regular dues use the equal-per-unit path, so DRE upload is optional here.'
          : 'Variable mode is active. Approve the DRE-backed assessment setup before final rendering.',
      href: `/hoa/${hoaId}/settings?section=dre&returnTo=/hoa/${hoaId}/disclosure`,
      icon: Landmark,
      status: assessmentMode === 'fixed' ? 'Ready' : 'Review setup',
    },
    {
      label: 'Assessment mapping',
      detail:
        assessmentMode === 'fixed'
          ? 'Fixed mode skips budget-to-pool mapping review for the regular assessment schedule.'
          : 'Approve DRE rules, aliases, residual routing, and current-year budget mappings.',
      href: `/hoa/${hoaId}/assessment-mapping-review`,
      icon: TableProperties,
      status: assessmentMode === 'fixed' ? 'Ready' : 'Review setup',
    },
    {
      label: 'Annual package state',
      detail: 'Create, approve, and finalize the annual package for this fiscal year.',
      href: `/hoa/${hoaId}/settings?section=packages&returnTo=/hoa/${hoaId}/disclosure`,
      icon: PackageCheck,
      status: 'Review setup',
    },
    {
      label: 'Appendices',
      detail: 'Confirm static policy appendices that should be bundled with the PDF.',
      href: `/hoa/${hoaId}/settings?section=appendices&returnTo=/hoa/${hoaId}/disclosure`,
      icon: FileArchive,
      status: 'Ready',
    },
  ];
}

export function DisclosureWorkspaceScreen() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [hoa, setHoa] = useState<HOARecord | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [packageLanguageOpen, setPackageLanguageOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadHOA() {
      if (!id) {
        setLoadError('HOA not found');
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      setLoadError(null);
      try {
        const selectedHoa = await getHOA(id);
        if (!cancelled) {
          setHoa(selectedHoa);
        }
      } catch (error) {
        if (!cancelled) {
          setLoadError(getErrorMessage(error, 'Failed to load disclosure workspace.'));
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void loadHOA();
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white">
        <p className="text-[#666666]">Loading disclosure workspace...</p>
      </div>
    );
  }

  if (loadError || !hoa || !id) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white">
        <p className="text-[#666666]">{loadError || 'HOA not found'}</p>
      </div>
    );
  }

  const fiscalYear = hoa.portfolio_year ?? new Date().getFullYear();
  const readinessRows = buildReadinessRows(id, hoa.assessment_mode);

  return (
    <div className="min-h-screen bg-[#fafafa]">
      <header className="sticky top-0 z-10 border-b border-[#e5e5e5] bg-white shadow-sm">
        <div className="flex flex-col gap-4 px-5 py-5 md:flex-row md:items-center md:justify-between md:px-8">
          <div className="flex min-w-0 items-center gap-4">
            <Link
              to={`/hoa/${id}`}
              className="rounded-lg p-2 transition-colors hover:bg-[#f5f5f5] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#111111]"
              aria-label="Back to budget workspace"
            >
              <ArrowLeft className="h-5 w-5 text-[#525252]" />
            </Link>
            <div className="min-w-0">
              <h1 className="text-xl font-semibold text-[#111111]">Disclosure Package</h1>
              <p className="truncate text-sm text-[#737373]">{hoa.name} · Fiscal Year {fiscalYear}</p>
            </div>
          </div>
          <Link to={`/hoa/${id}`}>
            <button
              type="button"
              className="inline-flex cursor-pointer items-center justify-center rounded-lg border border-[#d4d4d4] bg-white px-4 py-2 text-sm font-medium text-[#111111] transition-colors hover:border-[#a3a3a3] hover:bg-[#f5f5f5] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#111111]"
            >
              Back to Budget
            </button>
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-8 px-5 py-8 md:px-8">
        <section className="rounded-lg border border-[#e5e5e5] bg-white p-6 shadow-sm">
          <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div className="max-w-3xl space-y-2">
              <p className="text-xs font-medium uppercase tracking-[0.2em] text-[#737373]">
                Annual workflow
              </p>
              <h2 className="text-2xl font-semibold text-[#111111]">
                Generate the final disclosure PDF
              </h2>
              <p className="text-sm text-[#666666]">
                Review the required setup, finalize annual package context, then generate and download the disclosure package for board distribution.
              </p>
              <div className="mt-4 inline-flex flex-wrap items-center gap-2 rounded-full border border-[#e5e5e5] bg-[#fafafa] px-3 py-2 text-xs text-[#525252]">
                <span className="font-medium text-[#111111]">
                  Current assessment mode: {assessmentModeLabel(hoa.assessment_mode)}
                </span>
                <span className="text-[#d4d4d4]">•</span>
                <span>{assessmentModeWorkflowCopy(hoa.assessment_mode)}</span>
              </div>
            </div>
            <div className="inline-flex w-fit items-center gap-2 rounded-full border border-[#bbf7d0] bg-[#f0fdf4] px-3 py-1 text-xs font-medium text-[#166534]">
              <CheckCircle2 className="h-3.5 w-3.5" />
              {assessmentModeShortLabel(hoa.assessment_mode)} workflow ready
            </div>
          </div>
        </section>

        <section className="rounded-lg border border-[#e5e5e5] bg-white p-6 shadow-sm">
          <div className="mb-5 flex items-start gap-3">
            <FileText className="mt-0.5 h-5 w-5 text-[#525252]" />
            <div>
              <h2 className="text-lg font-semibold text-[#111111]">Readiness summary</h2>
              <p className="text-sm text-[#666666]">
                Use these links to fix source inputs before starting generation.
              </p>
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {readinessRows.map((row) => {
              const Icon = row.icon;
              const cardClassName =
                'group rounded-lg border border-[#e5e5e5] bg-white p-4 text-left transition-colors hover:border-[#a3a3a3] hover:bg-[#fafafa] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#111111]';
              const body = (
                  <div className="flex items-start gap-3">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#e5e5e5] bg-[#f7f7f7]">
                      <Icon className="h-4 w-4 text-[#525252]" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-3">
                        <h3 className="text-sm font-semibold text-[#111111]">{row.label}</h3>
                        <span
                          className={
                            row.status === 'Ready'
                              ? 'rounded-full bg-[#dcfce7] px-2 py-0.5 text-xs font-medium text-[#166534]'
                              : 'rounded-full bg-[#f5f5f5] px-2 py-0.5 text-xs font-medium text-[#525252]'
                          }
                        >
                          {row.status}
                        </span>
                      </div>
                      <p className="mt-1 text-sm leading-5 text-[#666666]">{row.detail}</p>
                    </div>
                  </div>
              );
              if (row.action === 'open-package-language') {
                return (
                  <button
                    key={row.label}
                    type="button"
                    className={`${cardClassName} w-full cursor-pointer`}
                    onClick={() => setPackageLanguageOpen(true)}
                  >
                    {body}
                  </button>
                );
              }
              return (
                <Link key={row.label} to={row.href!} className={cardClassName}>
                  {body}
                </Link>
              );
            })}
          </div>
        </section>

        <DisclosurePackagePanel
          hoaId={hoa.id}
          fiscalYear={fiscalYear}
          hoaName={hoa.name}
          isSupportedHoa
        />

        <section className="rounded-lg border border-[#e5e5e5] bg-white shadow-sm">
          <div className="border-b border-[#e5e5e5] px-6 py-5">
            <h2 className="text-lg font-semibold text-[#111111]">Annual package lifecycle</h2>
            <p className="mt-1 text-sm text-[#666666]">
              Create, approve, and finalize package records that support the generated disclosure PDF.
            </p>
          </div>
          <AnnualPackagesPanel hoaId={hoa.id} liveAssessmentMode={hoa.assessment_mode} />
        </section>
      </main>

      <BoilerplateWorkbench
        hoaId={hoa.id}
        hoaName={hoa.name}
        open={packageLanguageOpen}
        onClose={() => setPackageLanguageOpen(false)}
        // The editor opens from here as well as from Settings, so "Edit in
        // settings" has to leave this screen entirely. `field` is picked up by
        // SettingsScreen, which scrolls to and flashes it; `returnTo` brings
        // the operator back here afterwards.
        onEditSetting={(tab, field) =>
          navigate(
            `/hoa/${hoa.id}/settings?${new URLSearchParams({
              ...(tab === 'database' ? {} : { section: tab }),
              field,
              returnTo: `/hoa/${hoa.id}/disclosure`,
            })}`,
          )
        }
      />
    </div>
  );
}
