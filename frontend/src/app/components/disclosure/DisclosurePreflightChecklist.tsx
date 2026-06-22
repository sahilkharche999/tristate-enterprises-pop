// Stacked preflight checklist (UI-SPEC §6.1, §7.2, §9.3).
//
// Renders one row per finding. Each row reflects the live gate output:
// pass, fail (blocking), warning, or loading while the request is in flight.
// An "All clear" row appears when ready=true and there are no warnings.
//
// fixLinkPath enables a deep-link from a row to the field that needs attention.

import { AlertCircle, AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react';

export type PreflightRowStatus = 'pass' | 'fail' | 'warning' | 'loading' | 'unknown';

export interface PreflightRow {
  label: string;
  status: PreflightRowStatus;
  secondaryText?: string;
  fixLinkPath?: string;
  fixLinkLabel?: string;
}

// Kept for any existing callers; no longer used to drive the live checklist.
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
      return 'flex flex-col gap-0.5 rounded-md border-l-2 border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-sm text-[#b91c1c]';
    case 'warning':
      return 'flex flex-col gap-0.5 rounded-md border-l-2 border-[#fde68a] bg-[#fffbeb] px-3 py-2 text-sm text-[#92400e]';
    case 'pass':
      return 'flex flex-col gap-0.5 px-3 py-2 text-sm text-[#065f46]';
    case 'loading':
    case 'unknown':
    default:
      return 'flex flex-col gap-0.5 px-3 py-2 text-sm text-[#737373]';
  }
}

function StatusIcon({ status }: { status: PreflightRowStatus }) {
  switch (status) {
    case 'pass':
      return <CheckCircle2 aria-hidden className="h-4 w-4 shrink-0" />;
    case 'fail':
      return <AlertCircle aria-hidden className="h-4 w-4 shrink-0" />;
    case 'warning':
      return <AlertTriangle aria-hidden className="h-4 w-4 shrink-0" />;
    case 'loading':
      return (
        <Loader2 aria-hidden className="h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none" />
      );
    case 'unknown':
    default:
      return <AlertCircle aria-hidden className="h-4 w-4 shrink-0" />;
  }
}

const ALL_CLEAR_ROW: PreflightRow = {
  label: 'Disclosure package is ready to generate',
  status: 'pass',
};

export function DisclosurePreflightChecklist({
  rows,
  ready,
}: {
  rows: PreflightRow[];
  ready?: boolean;
}) {
  const displayRows =
    ready && rows.length === 0 ? [ALL_CLEAR_ROW] : rows;

  return (
    <ul className="space-y-2">
      {displayRows.map((row, i) => (
        <li key={`${row.label}-${i}`} className={rowClasses(row.status)}>
          <span className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2">
              <StatusIcon status={row.status} />
              <span>{row.label}</span>
            </span>
            {(row.status === 'fail' || row.status === 'warning') && row.fixLinkPath ? (
              <a href={row.fixLinkPath} className="shrink-0 text-sm underline">
                {row.fixLinkLabel ?? 'Fix'}
              </a>
            ) : null}
            {row.status === 'loading' ? (
              <span className="text-xs text-[#a3a3a3]">Checking…</span>
            ) : null}
          </span>
          {row.secondaryText ? (
            <p className="pl-6 text-xs opacity-80">{row.secondaryText}</p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
