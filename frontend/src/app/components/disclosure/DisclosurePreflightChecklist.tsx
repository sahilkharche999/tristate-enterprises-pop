// Stacked preflight checklist (UI-SPEC §6.1, §7.2, §9.3).
//
// For Phase 11 / Old Mill MVP the five rows render unconditionally as `pass`
// (preflight is implicit because the panel only mounts on the Old Mill
// workspace). Forward consistency: the component accepts an arbitrary list of
// rows so Phase 12+ can render real per-row pass/fail/loading states.
//
// Row labels are FIXED by UI-SPEC §9.3 — never paraphrase.

import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-react';

export type PreflightRowStatus = 'pass' | 'fail' | 'loading' | 'unknown';

export interface PreflightRow {
  label: string;
  status: PreflightRowStatus;
  fixLinkPath?: string;
  fixLinkLabel?: string;
}

// UI-SPEC §9.3 — verbatim row labels. Tuple, not array, so callers cannot
// mutate the canonical inventory.
export const OLD_MILL_PREFLIGHT_LABELS = [
  'Budget draft saved',
  'Reserve study attached',
  'HOA settings complete',
  'Reserve cash balance set',
  'Static appendices bundled',
] as const;

function rowClasses(status: PreflightRowStatus): string {
  switch (status) {
    case 'fail':
      return 'flex items-center justify-between rounded-md border-l-2 border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-sm text-[#b91c1c]';
    case 'pass':
      return 'flex items-center justify-between px-3 py-2 text-sm text-[#065f46]';
    case 'loading':
    case 'unknown':
    default:
      return 'flex items-center justify-between px-3 py-2 text-sm text-[#737373]';
  }
}

function StatusIcon({ status }: { status: PreflightRowStatus }) {
  switch (status) {
    case 'pass':
      return <CheckCircle2 aria-hidden className="h-4 w-4" />;
    case 'fail':
      return <AlertCircle aria-hidden className="h-4 w-4" />;
    case 'loading':
      return (
        <Loader2 aria-hidden className="h-4 w-4 animate-spin motion-reduce:animate-none" />
      );
    case 'unknown':
    default:
      return <AlertCircle aria-hidden className="h-4 w-4" />;
  }
}

export function DisclosurePreflightChecklist({ rows }: { rows: PreflightRow[] }) {
  return (
    <ul className="space-y-2">
      {rows.map((row) => (
        <li key={row.label} className={rowClasses(row.status)}>
          <span className="flex items-center gap-2">
            <StatusIcon status={row.status} />
            <span>{row.label}</span>
          </span>
          {row.status === 'fail' && row.fixLinkPath ? (
            <a href={row.fixLinkPath} className="text-sm underline">
              {row.fixLinkLabel ?? 'Fix'}
            </a>
          ) : null}
          {row.status === 'loading' ? <span>Checking...</span> : null}
          {row.status === 'unknown' ? <span>Status unavailable</span> : null}
        </li>
      ))}
    </ul>
  );
}
