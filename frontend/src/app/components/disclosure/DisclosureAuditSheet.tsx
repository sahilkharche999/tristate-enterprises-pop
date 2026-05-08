// Audit log right-side sheet (UI-SPEC §6.1, §7.6, §9.7).
//
// State machine: loading | loaded | loaded-empty | error
//
// On `open=true && jobId` change, fetch the audit log via
// getDisclosurePackageAudit(jobId) and render the resulting per-formula calls.
//
// Accessibility:
//   - role="dialog" + aria-modal="true" + aria-label="Audit log"
//   - ESC key closes the sheet (window keydown listener cleaned up on unmount)
//   - Backdrop click closes the sheet
//   - Close X button has aria-label="Close audit log"
//   - Each row toggles full input/output JSON expansion via button semantics
//
// Per UI-SPEC §9.7 the visible strings are exact:
//   "Audit Log", "No audit entries recorded for this run.",
//   "Could not load audit log.", "Retry",
//   per-row labels "Inputs", "Output", "Computed".

import { useEffect, useState } from 'react';
import { Loader2, X } from 'lucide-react';

import {
  type AuditLogEntry,
  type AuditLogResponse,
  getDisclosurePackageAudit,
} from '../../api/disclosurePackage';
import { Button } from '../ui/button';

export interface DisclosureAuditSheetProps {
  jobId: string | null;
  open: boolean;
  onClose: () => void;
}

type SheetState = 'loading' | 'loaded' | 'loaded-empty' | 'error';

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function summarize(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    const json = JSON.stringify(value);
    if (json.length <= 80) return json;
    return `${json.slice(0, 77)}...`;
  } catch {
    return String(value);
  }
}

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

interface AuditRowProps {
  entry: AuditLogEntry;
}

function AuditRow({ entry }: AuditRowProps) {
  const [expanded, setExpanded] = useState(false);
  const inputsSummary = summarize(entry.inputs);
  const outputSummary = summarize(entry.output);

  return (
    <li className="rounded-md border border-[#e5e5e5] bg-white p-3">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full flex-col gap-1 text-left"
        aria-expanded={expanded}
      >
        <span className="font-mono text-sm text-[#111111]">
          {entry.formula_id}
          <span className="ml-2 text-xs text-[#737373]">v{entry.version}</span>
        </span>
        <span className="truncate text-xs text-[#404040]">
          <span className="font-medium">Inputs</span>
          {' '}
          {inputsSummary}
        </span>
        <span className="truncate text-xs text-[#404040]">
          <span className="font-medium">Output</span>
          {' '}
          <span className="font-mono">{outputSummary}</span>
        </span>
        <span className="text-xs text-[#737373]">
          <span className="font-medium">Computed</span>
          {' '}
          {formatTimestamp(entry.computed_at)}
        </span>
      </button>
      {expanded ? (
        <div className="mt-2 space-y-2 border-t border-[#f5f5f5] pt-2">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-[#737373]">
              Inputs
            </p>
            <pre className="mt-1 max-h-48 overflow-auto rounded bg-[#f5f5f5] p-2 font-mono text-xs text-[#111111]">
              {formatJson(entry.inputs)}
            </pre>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-[#737373]">
              Output
            </p>
            <pre className="mt-1 max-h-48 overflow-auto rounded bg-[#f5f5f5] p-2 font-mono text-xs text-[#111111]">
              {formatJson(entry.output)}
            </pre>
          </div>
        </div>
      ) : null}
    </li>
  );
}

export function DisclosureAuditSheet({
  jobId,
  open,
  onClose,
}: DisclosureAuditSheetProps) {
  const [state, setState] = useState<SheetState>('loading');
  const [data, setData] = useState<AuditLogResponse | null>(null);
  const [fetchToken, setFetchToken] = useState(0);

  // Fetch audit log when the sheet opens (or when retry bumps fetchToken).
  useEffect(() => {
    if (!open || !jobId) return;
    let cancelled = false;
    setState('loading');
    setData(null);
    getDisclosurePackageAudit(jobId)
      .then((response) => {
        if (cancelled) return;
        setData(response);
        if (!response.formula_calls || response.formula_calls.length === 0) {
          setState('loaded-empty');
        } else {
          setState('loaded');
        }
      })
      .catch(() => {
        if (cancelled) return;
        setState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [open, jobId, fetchToken]);

  // ESC key closes the sheet (UI-SPEC §7.6).
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose]);

  if (!open) return null;

  const entries = data?.formula_calls ?? [];
  const subtitle = data
    ? `${entries.length} formula calls • generated at ${formatTimestamp(
        data.completed_at ?? data.started_at,
      )}`
    : '';

  return (
    <div className="fixed inset-0 z-50 flex">
      {/* Backdrop — clickable to close. */}
      <button
        type="button"
        aria-label="Close audit log"
        onClick={onClose}
        className="flex-1 bg-black/40"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Audit log"
        className="flex h-full w-full max-w-md flex-col overflow-y-auto bg-white shadow-xl"
      >
        <header className="flex items-start justify-between border-b border-[#e5e5e5] p-4">
          <div className="space-y-1">
            <h2 className="text-xl font-semibold text-[#111111]">Audit Log</h2>
            {subtitle ? (
              <p className="text-sm text-[#666666]">{subtitle}</p>
            ) : null}
          </div>
          <button
            type="button"
            aria-label="Close audit log"
            onClick={onClose}
            className="rounded-md p-1 text-[#737373] hover:bg-[#f5f5f5] hover:text-[#111111]"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex-1 p-4">
          {state === 'loading' ? (
            <div className="space-y-2" aria-busy="true">
              {Array.from({ length: 5 }).map((_, idx) => (
                <div
                  key={idx}
                  className="h-12 animate-pulse rounded-md bg-[#f5f5f5]"
                />
              ))}
            </div>
          ) : null}

          {state === 'loaded-empty' ? (
            <div className="flex h-full items-center justify-center text-center">
              <p className="text-sm text-[#737373]">
                No audit entries recorded for this run.
              </p>
            </div>
          ) : null}

          {state === 'loaded' ? (
            <ul className="space-y-2">
              {entries.map((entry, idx) => (
                <AuditRow key={`${entry.formula_id}-${idx}`} entry={entry} />
              ))}
            </ul>
          ) : null}

          {state === 'error' ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
              <p className="text-sm text-[#b91c1c]">
                Could not load audit log.
              </p>
              <Button
                variant="outline"
                onClick={() => setFetchToken((v) => v + 1)}
                className="border-[#d4d4d4] text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
              >
                <Loader2 className="mr-2 h-4 w-4" aria-hidden="true" />
                Retry
              </Button>
            </div>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
