// Stage-chip strip + elapsed-time block (UI-SPEC §6.1, §7.3, §9.4).
//
// Five visible stages, in order: Validating → Computing → Rendering →
// Merging → Ready. Each chip is one of three states (done / active / pending)
// keyed off the current stage index. Long-running notice fades in at 30s
// elapsed (UI-SPEC §8.6 / §9.4).

import { CheckCircle2, Loader2 } from 'lucide-react';

import type { DisclosurePackageStage } from '../../api/disclosurePackage';
import { STAGE_LABEL, STAGE_ORDER, getJobStageColor } from '../../lib/jobStageColors';

const VISIBLE_STAGES: Array<Exclude<DisclosurePackageStage, null>> = [
  'validating',
  'computing',
  'rendering',
  'merging',
  'verifying',
];

const LONG_RUNNING_THRESHOLD_MS = 30_000;

function formatElapsed(ms: number): string {
  // UI-SPEC §9.4: "Running for 0m 14s".
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s`;
}

export interface DisclosureProgressBlockProps {
  currentStage: DisclosurePackageStage;
  elapsedMs: number;
}

export function DisclosureProgressBlock({
  currentStage,
  elapsedMs,
}: DisclosureProgressBlockProps) {
  const currentIdx = currentStage ? STAGE_ORDER.indexOf(currentStage) : -1;
  const showLongRunning = elapsedMs >= LONG_RUNNING_THRESHOLD_MS;

  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className="space-y-3 transition-opacity duration-200"
    >
      <p className="text-xs font-medium uppercase tracking-[0.2em] text-[#737373]">
        Generation Progress
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {VISIBLE_STAGES.map((stageName, idx) => {
          const chipState =
            idx < currentIdx ? 'done' : idx === currentIdx ? 'active' : 'pending';
          const colors = getJobStageColor(chipState);
          const label = STAGE_LABEL[stageName] ?? stageName;
          const ariaState =
            chipState === 'active'
              ? 'in progress'
              : chipState === 'done'
                ? 'complete'
                : 'pending';
          return (
            <span
              key={stageName}
              role="status"
              aria-label={`${label}: ${ariaState}`}
              style={{
                backgroundColor: colors.bg,
                color: colors.text,
                borderColor: colors.border,
              }}
              className="inline-flex items-center gap-1 rounded-full border px-2 py-1 text-xs"
            >
              {chipState === 'done' ? (
                <CheckCircle2 aria-hidden className="h-3.5 w-3.5" />
              ) : null}
              {chipState === 'active' ? (
                <Loader2
                  aria-hidden
                  className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
                />
              ) : null}
              {chipState === 'pending' ? (
                <span aria-hidden className="block h-2 w-2 rounded-full border" />
              ) : null}
              <span>{label}</span>
            </span>
          );
        })}
      </div>
      <p className="text-sm text-[#404040]">Running for {formatElapsed(elapsedMs)}</p>
      {showLongRunning ? (
        <p
          role="status"
          className="rounded-md border border-[#fde68a] bg-[#fef3c7] px-3 py-2 text-sm text-[#92400e] transition-opacity duration-200"
        >
          Taking longer than expected. Generation typically completes in 10-20 seconds.
        </p>
      ) : null}
    </div>
  );
}
