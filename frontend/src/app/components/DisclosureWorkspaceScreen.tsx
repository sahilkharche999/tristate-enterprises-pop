import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
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
import { getDisclosurePreflight, type ReadinessStep } from '../api/disclosurePackage';
import { getHOA, type HOARecord } from '../api/hoa';
import { getErrorMessage } from '../lib/errors';
import {
  assessmentModeLabel,
  assessmentModeWorkflowCopy,
  type AssessmentMode,
} from '../lib/assessmentMode';
import {
  buildSettingsEditHref,
  stripOpenPackageLanguageParam,
  wantsOpenPackageLanguage,
  withOpenPackageLanguagePath,
} from '../lib/settingsNavigation';
import { Button } from './ui/button';

const STEP_ICONS: Record<string, typeof TableProperties> = {
  budget_draft: TableProperties,
  reserve_study: ClipboardCheck,
  disclosure_settings: Settings,
  assessment_setup: Landmark,
  assessment_mapping: TableProperties,
  appendices: FileArchive,
  annual_package: PackageCheck,
  package_language: PenLine,
};

function statusBadge(status: ReadinessStep['status']): { label: string; className: string } {
  switch (status) {
    case 'done':
      return { label: 'Done', className: 'rounded-full bg-[#dcfce7] px-2 py-0.5 text-xs font-medium text-[#166534]' };
    case 'needs_action':
      return { label: 'Needs you', className: 'rounded-full bg-[#fee2e2] px-2 py-0.5 text-xs font-medium text-[#b91c1c]' };
    case 'warning':
      return { label: 'Warning', className: 'rounded-full bg-[#fef3c7] px-2 py-0.5 text-xs font-medium text-[#92400e]' };
    case 'not_required':
    default:
      return { label: 'N/A', className: 'rounded-full bg-[#f5f5f5] px-2 py-0.5 text-xs font-medium text-[#525252]' };
  }
}

export function DisclosureWorkspaceScreen() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [hoa, setHoa] = useState<HOARecord | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [packageLanguageOpen, setPackageLanguageOpen] = useState(false);
  const [steps, setSteps] = useState<ReadinessStep[]>([]);
  const [preflightReady, setPreflightReady] = useState<boolean | null>(null);
  const [interceptOpen, setInterceptOpen] = useState(false);

  // Settings Back used returnTo=…?openPackageLanguage=1 — reopen the workbench.
  useEffect(() => {
    if (!hoa) return;
    if (!wantsOpenPackageLanguage(searchParams)) return;
    setPackageLanguageOpen(true);
    setSearchParams(
      (current) => stripOpenPackageLanguageParam(current),
      { replace: true },
    );
  }, [hoa, searchParams, setSearchParams]);

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

  const fiscalYear = hoa?.portfolio_year ?? new Date().getFullYear();

  useEffect(() => {
    if (!hoa) return;
    let cancelled = false;
    void (async () => {
      try {
        const pf = await getDisclosurePreflight(hoa.id, fiscalYear);
        if (cancelled) return;
        setSteps(pf.steps ?? []);
        setPreflightReady(pf.ready);
        const needs = (pf.steps ?? []).filter((s) => s.status === 'needs_action');
        const key = `disclosure-intercept:${hoa.id}:${fiscalYear}`;
        if (needs.length > 0 && !sessionStorage.getItem(key)) {
          setInterceptOpen(true);
        }
      } catch {
        if (!cancelled) {
          setSteps([]);
          setPreflightReady(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [hoa, fiscalYear]);

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

  const applicableSteps = steps.filter((s) => s.status !== 'not_required');
  const doneCount = applicableSteps.filter((s) => s.status === 'done').length;
  const nextStep = steps.find((s) => s.status === 'needs_action');
  const packageLanguageStep: ReadinessStep = {
    id: 'package_language',
    label: 'Package language',
    status: 'done',
    detail: 'Optional: edit cover-letter wording in the full-screen workbench.',
    fix_path: undefined,
    fix_label: 'Edit language',
  };
  const displaySteps = steps.length > 0 ? [...steps, packageLanguageStep] : steps;

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
          <div className="flex flex-wrap items-center gap-2">
            {nextStep?.fix_path ? (
              <Link to={nextStep.fix_path}>
                <Button type="button" className="cursor-pointer">
                  Next: {nextStep.label}
                </Button>
              </Link>
            ) : null}
            <Link to={`/hoa/${id}`}>
              <button
                type="button"
                className="inline-flex cursor-pointer items-center justify-center rounded-lg border border-[#d4d4d4] bg-white px-4 py-2 text-sm font-medium text-[#111111] transition-colors hover:border-[#a3a3a3] hover:bg-[#f5f5f5] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#111111]"
              >
                Back to Budget
              </button>
            </Link>
          </div>
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
              <p className="rounded-md border border-[#e5e5e5] bg-[#fafafa] px-3 py-2 text-sm text-[#525252]">
                <strong className="text-[#111111]">What this page is for:</strong> final gate to
                build the homeowner disclosure package PDF. This is not the Budget “Generate Budget”
                action — finish mapping and disclosure settings first when the checklist says so.
              </p>
              <p className="text-sm text-[#666666]">
                Review the required setup, then generate and download the disclosure package for
                board distribution.
              </p>
              <div className="mt-4 inline-flex flex-wrap items-center gap-2 rounded-full border border-[#e5e5e5] bg-[#fafafa] px-3 py-2 text-xs text-[#525252]">
                <span className="font-medium text-[#111111]">
                  Current assessment mode: {assessmentModeLabel(hoa.assessment_mode)}
                </span>
                <span className="text-[#d4d4d4]">•</span>
                <span>{assessmentModeWorkflowCopy(hoa.assessment_mode)}</span>
              </div>
            </div>
            {preflightReady === true ? (
              <div className="inline-flex w-fit items-center gap-2 rounded-full border border-[#bbf7d0] bg-[#f0fdf4] px-3 py-1 text-xs font-medium text-[#166534]">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Ready to generate
              </div>
            ) : preflightReady === false ? (
              <div className="inline-flex w-fit items-center gap-2 rounded-full border border-[#fecaca] bg-[#fef2f2] px-3 py-1 text-xs font-medium text-[#b91c1c]">
                Not ready — fix checklist items
              </div>
            ) : (
              <div className="inline-flex w-fit items-center gap-2 rounded-full border border-[#e5e5e5] bg-[#f5f5f5] px-3 py-1 text-xs font-medium text-[#525252]">
                Checking readiness…
              </div>
            )}
          </div>
        </section>

        <section className="rounded-lg border border-[#e5e5e5] bg-white p-6 shadow-sm">
          <div className="mb-5 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex items-start gap-3">
              <FileText className="mt-0.5 h-5 w-5 text-[#525252]" />
              <div>
                <h2 className="text-lg font-semibold text-[#111111]">Readiness summary</h2>
                <p className="text-sm text-[#666666]">
                  Live status from preflight (not a static checklist).{' '}
                  {applicableSteps.length > 0
                    ? `${doneCount}/${applicableSteps.length} applicable steps done.`
                    : 'Loading…'}
                </p>
              </div>
            </div>
            {nextStep?.fix_path ? (
              <Link to={nextStep.fix_path}>
                <Button type="button" variant="outline" className="cursor-pointer">
                  Next: {nextStep.label}
                </Button>
              </Link>
            ) : null}
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {displaySteps.map((step) => {
              const Icon = STEP_ICONS[step.id] ?? TableProperties;
              const badge = statusBadge(step.status);
              const cardClassName =
                'group rounded-lg border border-[#e5e5e5] bg-white p-4 text-left transition-colors hover:border-[#a3a3a3] hover:bg-[#fafafa] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#111111]';
              const body = (
                <div className="flex items-start gap-3">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#e5e5e5] bg-[#f7f7f7]">
                    <Icon className="h-4 w-4 text-[#525252]" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-3">
                      <h3 className="text-sm font-semibold text-[#111111]">{step.label}</h3>
                      <span className={badge.className}>{badge.label}</span>
                    </div>
                    <p className="mt-1 text-sm leading-5 text-[#666666]">
                      {step.detail || 'Open to review.'}
                    </p>
                  </div>
                </div>
              );
              if (step.id === 'package_language') {
                return (
                  <button
                    key={step.id}
                    type="button"
                    className={`${cardClassName} w-full cursor-pointer`}
                    onClick={() => setPackageLanguageOpen(true)}
                  >
                    {body}
                  </button>
                );
              }
              const href = step.fix_path || `/hoa/${id}`;
              return (
                <Link key={step.id} to={href} className={cardClassName}>
                  {body}
                </Link>
              );
            })}
          </div>
        </section>

        {interceptOpen ? (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
            <div className="max-w-lg rounded-xl border border-[#e5e5e5] bg-white p-6 shadow-xl">
              <h3 className="text-lg font-semibold text-[#111111]">Finish required setup first</h3>
              <p className="mt-2 text-sm text-[#525252]">
                Some steps still need attention before the homeowner PDF can generate correctly
                (for variable HOAs this often includes assessment mapping).
              </p>
              <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-[#111111]">
                {steps
                  .filter((s) => s.status === 'needs_action')
                  .map((s) => (
                    <li key={s.id}>{s.label}</li>
                  ))}
              </ul>
              <div className="mt-5 flex flex-wrap justify-end gap-2">
                <Button
                  type="button"
                  variant="outline"
                  className="cursor-pointer"
                  onClick={() => {
                    sessionStorage.setItem(
                      `disclosure-intercept:${hoa.id}:${fiscalYear}`,
                      '1',
                    );
                    setInterceptOpen(false);
                  }}
                >
                  I&apos;ll fix later
                </Button>
                {nextStep?.fix_path ? (
                  <Link
                    to={nextStep.fix_path}
                    onClick={() => {
                      sessionStorage.setItem(
                        `disclosure-intercept:${hoa.id}:${fiscalYear}`,
                        '1',
                      );
                      setInterceptOpen(false);
                    }}
                  >
                    <Button type="button" className="cursor-pointer">
                      Fix: {nextStep.label}
                    </Button>
                  </Link>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}

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
          <AnnualPackagesPanel
            hoaId={hoa.id}
            liveAssessmentMode={hoa.assessment_mode}
            defaultPackageYear={fiscalYear}
          />
        </section>
      </main>

      <BoilerplateWorkbench
        hoaId={hoa.id}
        hoaName={hoa.name}
        packageYear={fiscalYear}
        open={packageLanguageOpen}
        onClose={() => setPackageLanguageOpen(false)}
        // Cross-route: navigate to Settings with field + returnTo that reopens
        // this package-language workbench when the operator hits Back.
        onEditSetting={(tab, field) =>
          navigate(
            buildSettingsEditHref({
              hoaId: hoa.id,
              tab,
              field,
              returnTo: withOpenPackageLanguagePath(
                `/hoa/${hoa.id}/disclosure`,
              ),
            }),
          )
        }
      />
    </div>
  );
}
