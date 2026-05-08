// Top-level disclosure-package panel (UI-SPEC §5.1, §6.1, §7.1).
//
// State machine:
//   - idle (ready)   — preflight (all-pass) + enabled Generate button
//   - idle (locked)  — unsupported HOA: disabled Generate + locked body copy
//   - starting       — POST in flight; CTA → "Starting..." disabled
//   - running        — progress block with stage chips + elapsed
//   - completed      — result block with Download / Regenerate
//   - failed         — failure block with Retry
//
// The panel is mounted in BudgetScreenWrapper.tsx for every HOA workspace
// (UI-SPEC §5.2 visibility rule). Old Mill is the only `isSupportedHoa=true`
// HOA in Phase 11; other HOAs see the locked state.
//
// Card chrome verbatim from ReserveStudyView.tsx:65-105 (PATTERNS analog).
// Visible strings come from UI-SPEC §9 verbatim.

import { useDisclosureJob } from './useDisclosureJob';
import {
  DisclosurePreflightChecklist,
  OLD_MILL_PREFLIGHT_LABELS,
  type PreflightRow,
} from './DisclosurePreflightChecklist';
import { DisclosureProgressBlock } from './DisclosureProgressBlock';
import { DisclosureResultBlock } from './DisclosureResultBlock';
import { DisclosureFailureBlock } from './DisclosureFailureBlock';
import { Button } from '../ui/button';

export interface DisclosurePackagePanelProps {
  hoaId: number;
  fiscalYear: number;
  hoaName: string;
  // True only for "Old Mill Homeowners Association" in Phase 11. Forward-
  // compatible with a backend-derived `disclosure_supported` boolean per
  // UI-SPEC OQ-3.
  isSupportedHoa: boolean;
}

const SUPPORTED_PREFLIGHT_PASS: PreflightRow[] = OLD_MILL_PREFLIGHT_LABELS.map(
  (label) => ({ label, status: 'pass' as const }),
);

export function DisclosurePackagePanel({
  hoaId,
  fiscalYear,
  hoaName,
  isSupportedHoa,
}: DisclosurePackagePanelProps) {
  const { state, job, stage, elapsedMs, error, generate, reset } = useDisclosureJob();

  const handleGenerate = () => {
    if (!isSupportedHoa) return;
    void generate(hoaId, fiscalYear);
  };

  const handleRetry = () => {
    reset();
    if (!isSupportedHoa) return;
    void generate(hoaId, fiscalYear);
  };

  const handleRegenerate = () => {
    reset();
    if (!isSupportedHoa) return;
    void generate(hoaId, fiscalYear);
  };

  // UI-SPEC §9.1 — verbatim copy strings. Body interpolates `hoa.name` and
  // `fiscal_year` when supported; falls back to the locked-out line for
  // unsupported HOAs.
  const supportedBody = `Compile the full annual budget disclosure PDF for ${hoaName}'s ${fiscalYear} fiscal year, including the cover letter, pro forma operating budget, reserve disclosure, 30-year funding plan, and required policy appendices.`;
  const lockedBody = 'Disclosure package generation is not yet available for this HOA.';
  const bodyCopy = isSupportedHoa ? supportedBody : lockedBody;

  const showCta = state === 'idle' || state === 'starting';

  return (
    <section className="space-y-6">
      <div className="rounded-2xl border border-[#e5e5e5] bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-[0.2em] text-[#737373]">
              Disclosure Package
            </p>
            <h2 className="text-xl font-semibold text-[#111111]">
              Generate {fiscalYear} Disclosure Package
            </h2>
            <p className="max-w-2xl text-sm text-[#666666]">{bodyCopy}</p>
          </div>
          {showCta ? (
            <div className="flex flex-wrap items-center gap-2 md:flex-nowrap md:justify-end">
              {state === 'starting' ? (
                <Button
                  disabled
                  className="whitespace-nowrap bg-[#111111] text-white opacity-50 shadow-sm"
                >
                  Starting...
                </Button>
              ) : (
                <Button
                  onClick={handleGenerate}
                  disabled={!isSupportedHoa}
                  className="whitespace-nowrap bg-[#111111] text-white shadow-sm hover:bg-[#262626] disabled:opacity-50"
                >
                  Generate Disclosure Package
                </Button>
              )}
            </div>
          ) : null}
        </div>

        <div className="mt-6">
          {state === 'idle' && isSupportedHoa ? (
            <DisclosurePreflightChecklist rows={SUPPORTED_PREFLIGHT_PASS} />
          ) : null}
          {state === 'starting' || state === 'running' ? (
            <DisclosureProgressBlock currentStage={stage} elapsedMs={elapsedMs} />
          ) : null}
          {state === 'completed' && job ? (
            <DisclosureResultBlock job={job} onRegenerate={handleRegenerate} />
          ) : null}
          {state === 'failed' ? (
            <DisclosureFailureBlock
              errorMessage={error ?? ''}
              stage={typeof stage === 'string' ? stage : null}
              onRetry={handleRetry}
            />
          ) : null}
        </div>
      </div>
    </section>
  );
}
