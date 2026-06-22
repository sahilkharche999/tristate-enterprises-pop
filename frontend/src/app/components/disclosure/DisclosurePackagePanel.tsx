// Top-level disclosure-package panel (UI-SPEC §5.1, §6.1, §7.1).
//
// State machine:
//   - idle (ready)   — live preflight checklist + Generate button (gated on blocking items)
//   - idle (locked)  — unsupported HOA: disabled Generate + locked body copy
//   - starting       — POST in flight; CTA → "Starting..." disabled
//   - running        — progress block with stage chips + elapsed
//   - completed      — result block with Download / Regenerate
//   - failed         — failure block with Retry
//
// The preflight checklist is fetched live on mount and on explicit re-check.
// The Generate button is disabled while blocking preflight findings exist.

import { useCallback, useEffect, useState } from 'react';

import {
  getDisclosurePreflight,
  type DisclosurePreflightResult,
} from '../../api/disclosurePackage';
import { useDisclosureJob } from './useDisclosureJob';
import {
  DisclosurePreflightChecklist,
  type PreflightRow,
} from './DisclosurePreflightChecklist';
import { DisclosureProgressBlock } from './DisclosureProgressBlock';
import { DisclosureResultBlock } from './DisclosureResultBlock';
import { DisclosureFailureBlock } from './DisclosureFailureBlock';
import { DisclosureAuditSheet } from './DisclosureAuditSheet';
import { DisclosureAppendixManager } from './DisclosureAppendixManager';
import { Button } from '../ui/button';

export interface DisclosurePackagePanelProps {
  hoaId: number;
  fiscalYear: number;
  hoaName: string;
  // Phase-11 gate; always true now (is_supported_hoa retired). Kept on
  // signature so parent components don't need to be updated simultaneously.
  isSupportedHoa: boolean;
}

function findingsToRows(result: DisclosurePreflightResult): PreflightRow[] {
  const rows: PreflightRow[] = [];
  for (const e of result.blocking) {
    rows.push({
      label: e.message,
      status: 'fail',
      secondaryText: e.suggested_fix ?? undefined,
    });
  }
  for (const e of result.warnings) {
    rows.push({
      label: e.message,
      status: 'warning',
      secondaryText: e.suggested_fix ?? undefined,
    });
  }
  return rows;
}

function loadingRows(): PreflightRow[] {
  return [{ label: 'Checking readiness…', status: 'loading' }];
}

export function DisclosurePackagePanel({
  hoaId,
  fiscalYear,
  hoaName,
  isSupportedHoa,
}: DisclosurePackagePanelProps) {
  const { state, job, stage, elapsedMs, error, generate, reset } = useDisclosureJob();
  const [auditOpen, setAuditOpen] = useState(false);

  const [preflight, setPreflight] = useState<DisclosurePreflightResult | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);

  const fetchPreflight = useCallback(async () => {
    setPreflightLoading(true);
    try {
      const result = await getDisclosurePreflight(hoaId, fiscalYear);
      setPreflight(result);
    } catch {
      // On error, treat as unknown readiness (don't block the operator).
      setPreflight(null);
    } finally {
      setPreflightLoading(false);
    }
  }, [hoaId, fiscalYear]);

  useEffect(() => {
    void fetchPreflight();
  }, [fetchPreflight]);

  const blockingCount = preflight?.blocking.length ?? 0;
  const isGenerateBlocked =
    !isSupportedHoa || (preflight !== null && blockingCount > 0);

  const handleGenerate = () => {
    if (isGenerateBlocked) return;
    void generate(hoaId, fiscalYear);
  };

  const handleRetry = () => {
    reset();
    if (!isGenerateBlocked) void generate(hoaId, fiscalYear);
  };

  const handleRegenerate = () => {
    reset();
    if (!isGenerateBlocked) void generate(hoaId, fiscalYear);
  };

  const supportedBody = `Compile the full annual budget disclosure PDF for ${hoaName}'s ${fiscalYear} fiscal year, including the cover letter, pro forma operating budget, reserve disclosure, 30-year funding plan, and required policy appendices.`;
  const lockedBody = 'Disclosure package generation is not yet available for this HOA.';
  const bodyCopy = isSupportedHoa ? supportedBody : lockedBody;

  const showCta = state === 'idle' || state === 'starting';

  // Rows fed to the live checklist while the panel is idle.
  const preflightRows: PreflightRow[] = preflightLoading
    ? loadingRows()
    : preflight !== null
      ? findingsToRows(preflight)
      : [];

  const generateButtonLabel =
    state === 'starting'
      ? 'Starting…'
      : blockingCount > 0
        ? `Resolve ${blockingCount} item${blockingCount === 1 ? '' : 's'} to generate`
        : 'Generate Disclosure Package';

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
              <Button
                onClick={handleGenerate}
                disabled={state === 'starting' || isGenerateBlocked}
                className="whitespace-nowrap bg-[#111111] text-white shadow-sm hover:bg-[#262626] disabled:opacity-50"
              >
                {generateButtonLabel}
              </Button>
              {state === 'idle' && (
                <Button
                  variant="outline"
                  onClick={() => void fetchPreflight()}
                  disabled={preflightLoading}
                  className="whitespace-nowrap text-xs disabled:opacity-50"
                >
                  {preflightLoading ? 'Checking…' : 'Re-check'}
                </Button>
              )}
            </div>
          ) : null}
        </div>

        <div className="mt-6">
          {state === 'idle' && isSupportedHoa ? (
            <DisclosurePreflightChecklist
              rows={preflightRows}
              ready={preflight?.ready ?? false}
            />
          ) : null}
          {state === 'starting' || state === 'running' ? (
            <DisclosureProgressBlock currentStage={stage} elapsedMs={elapsedMs} />
          ) : null}
          {state === 'completed' && job ? (
            <DisclosureResultBlock
              job={job}
              onRegenerate={handleRegenerate}
              onViewAudit={() => setAuditOpen(true)}
            />
          ) : null}
          {state === 'failed' ? (
            <DisclosureFailureBlock
              errorMessage={error ?? ''}
              stage={typeof stage === 'string' ? stage : null}
              onRetry={handleRetry}
            />
          ) : null}
        </div>

        {isSupportedHoa ? (
          <div className="mt-6 border-t border-[#e5e5e5] pt-6">
            <DisclosureAppendixManager
              hoaId={hoaId}
              disabled={state === 'starting' || state === 'running'}
            />
          </div>
        ) : null}
      </div>
      <DisclosureAuditSheet
        jobId={job?.id ?? null}
        open={auditOpen}
        onClose={() => setAuditOpen(false)}
      />
    </section>
  );
}
