// DRE Review Workbench page (Phase 4 tasks 98-103, 126-127, 131).
//
// Operator-facing screen for reviewing a Gemini Vision DRE extraction
// before promoting it to a live AssessmentSetup. Shows:
//   - extracted setup_type, pools, groups/units, formulas, warnings
//   - source-page citations per entity (popup on click)
//   - low-confidence flags + validation warnings prominently at top
//   - editable values that write to dre_review_edits on save
//   - edit-history sidebar
//   - approve action (promotes to AssessmentSetup with chosen setup_type)
//
// Path: ``/hoa/{hoaId}/dre/review/{runId}`` — wired via routes (TODO when
// the app router lands; for now exported as a standalone panel).

import {
  Fragment,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import {
  type DREExtractionRunDetail,
  type DREReviewEdit,
  type ReopenRepromoteResponse,
  approveExtractionRun,
  dreDocumentFileUrl,
  getExtractionRun,
  listReviewEdits,
  recordReviewEdit,
  reopenAndRepromoteExtractionRun,
} from '../api/dre';
import { approveCCRRun, reopenAndRepromoteCCRRun } from '../api/ccr';
import { formatCurrency } from '../lib/budget';
import { cn } from './ui/utils';
import { DrePdfCompareView } from './DrePdfCompareView';

// Lets the shared PageChips component (used ~10x across this file) call
// into whatever owns compare-view state without prop-drilling through every
// intermediate renderer (pools, groups, units, factors, categories, ...).
// See openspec/changes/add-dre-review-pdf-compare-view design.md Decision 1b.
const PdfJumpContext = createContext<((page: number) => void) | null>(null);

// ── API error formatting ──────────────────────────────────────────────
//
// handleResponse() throws `{ status, message }` where `message` is
// JSON.stringify(body.detail) whenever the backend sends a structured
// 422 body (UnresolvableReviewEdit / EditedEntityFailedToPromote /
// MissingUnitFactors). Parse that back out so the operator sees the
// specific field paths / entities / pool keys, not a raw JSON blob.
function describeApiError(exc: unknown): string {
  const raw =
    exc && typeof exc === 'object' && 'message' in exc
      ? String((exc as { message?: unknown }).message ?? '')
      : String(exc);
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      const message = typeof parsed.message === 'string' ? parsed.message : null;
      if (Array.isArray(parsed.unresolvable_field_paths) && parsed.unresolvable_field_paths.length > 0) {
        return `${message || 'Unresolvable review edit(s).'} Field path(s): ${parsed.unresolvable_field_paths.join(', ')}`;
      }
      if (Array.isArray(parsed.failed_entities) && parsed.failed_entities.length > 0) {
        return `${message || "These edited items couldn't be promoted."} ${parsed.failed_entities.join(', ')}`;
      }
      if (Array.isArray(parsed.missing_pool_keys) && parsed.missing_pool_keys.length > 0) {
        return `${message || 'Missing per-unit factors.'} Pool(s): ${parsed.missing_pool_keys.join(', ')}`;
      }
      if (message) return message;
    }
  } catch {
    // Not JSON — fall through to the raw string.
  }
  return raw || 'Unknown error';
}

const ALLOCATION_METHOD_OPTIONS = [
  'equal',
  'square_footage',
  'ownership_percentage',
  'category',
  'specified_value',
  'parking_space',
  'custom_factor',
  'unknown',
];

const DENOMINATOR_SOURCE_OPTIONS = ['dre_shown', 'calculated', 'unknown'];

// Mirrors the allocation_pools.recipient_scope CHECK constraint and
// promotion.py::_coerce_recipient_scope's recognized values. A select (not
// free text) so an operator can't type a typo that silently falls back to
// "all_units" with no error.
const RECIPIENT_SCOPE_OPTIONS = [
  'all_units',
  'residential_only',
  'commercial_only',
  'parking_users',
  'custom_unit_list',
];

// ── Inline-editable value ─────────────────────────────────────────────
//
// Every field the Review Workbench renders read-only-with-a-pencil goes
// through this: click to edit, Save writes a dre_review_edits row via
// onSave, Cancel discards the draft. editCount surfaces "N edits so far"
// so last-write-wins isn't silent (list_review_edits already returns full
// history; this just makes it visible next to the field itself).
function EditableValue({
  value,
  fieldPath,
  onSave,
  disabled,
  editCount,
  kind = 'text',
  options,
  display,
}: {
  value: unknown;
  fieldPath: string;
  onSave: (fieldPath: string, oldValue: unknown, newValue: unknown) => void;
  disabled?: boolean;
  editCount?: number;
  kind?: 'text' | 'number' | 'select' | 'boolean-select';
  options?: string[];
  display?: React.ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  function startEdit() {
    setDraft(value == null ? '' : String(value));
    setEditing(true);
  }

  function commit() {
    let newValue: unknown = draft;
    if (kind === 'number') newValue = draft === '' ? null : Number(draft);
    if (kind === 'boolean-select') newValue = draft === 'true';
    setEditing(false);
    if (newValue !== value) onSave(fieldPath, value, newValue);
  }

  if (editing) {
    return (
      <span className="inline-flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
        {kind === 'select' || kind === 'boolean-select' ? (
          <select
            autoFocus
            className="rounded border border-slate-300 px-1 py-0.5 text-xs"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          >
            {kind === 'boolean-select'
              ? ['true', 'false'].map((o) => (
                  <option key={o} value={o}>
                    {o === 'true' ? 'Yes' : 'No'}
                  </option>
                ))
              : (options || []).map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
          </select>
        ) : (
          <input
            autoFocus
            type={kind === 'number' ? 'number' : 'text'}
            min={kind === 'number' ? '0' : undefined}
            step={kind === 'number' ? '0.01' : undefined}
            className="w-24 rounded border border-slate-300 px-1 py-0.5 text-xs"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commit();
              if (e.key === 'Escape') setEditing(false);
            }}
          />
        )}
        <button
          type="button"
          className="rounded bg-emerald-600 px-1.5 py-0.5 text-[11px] font-medium text-white hover:bg-emerald-700"
          onClick={commit}
        >
          Save
        </button>
        <button
          type="button"
          className="rounded border border-slate-300 px-1.5 py-0.5 text-[11px] text-slate-600 hover:bg-slate-50"
          onClick={() => setEditing(false)}
        >
          Cancel
        </button>
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5">
      <span>{display ?? (value == null || value === '' ? <span className="text-slate-400">—</span> : String(value))}</span>
      {!disabled && (
        <button
          type="button"
          aria-label={`Edit ${fieldPath}`}
          title="Edit"
          className="text-slate-300 hover:text-slate-700"
          onClick={startEdit}
        >
          ✎
        </button>
      )}
      {Boolean(editCount) && (
        <span
          title={`${editCount} edit${editCount === 1 ? '' : 's'} to this field — see Edit history below`}
          className="inline-flex items-center rounded-full bg-indigo-50 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700 ring-1 ring-inset ring-indigo-600/20"
        >
          {editCount}✎
        </span>
      )}
    </span>
  );
}

type Props = {
  hoaId: number;
  runId: number;
};

const EXTRACTION_REFRESH_MS = 2000;
const ACTIVE_JOB_STATUSES = new Set(['queued', 'running']);

const SETUP_TYPE_LABELS: Record<'fixed' | 'grouped' | 'per_unit', string> = {
  fixed: 'Fixed (all units pay the same)',
  grouped: 'Grouped (per-category dues)',
  per_unit: 'Per-unit (each unit has its own value)',
};

const PROMPT_SETUP_TYPE_TO_INTERNAL: Record<string, 'fixed' | 'grouped' | 'per_unit'> = {
  fixed_equal: 'fixed',
  grouped_category: 'grouped',
  individual_unit: 'per_unit',
  multi_pool_combination: 'per_unit',
  unknown_needs_review: 'fixed',
};

function suggestedInternalSetupType(
  parsed: Record<string, unknown> | null,
): 'fixed' | 'grouped' | 'per_unit' {
  const setup = (parsed?.assessment_setup || {}) as Record<string, unknown>;
  const unitStructure = (parsed?.unit_structure || {}) as Record<string, unknown>;
  const displayMode = String(setup.display_mode || '').toLowerCase();
  const promptSetupType = String(setup.setup_type || '');
  const groups = Array.isArray(unitStructure.groups) ? unitStructure.groups : [];
  const units = Array.isArray(unitStructure.units) ? unitStructure.units : [];

  if (displayMode === 'fixed') return 'fixed';
  if (displayMode === 'grouped') return 'grouped';
  if (displayMode === 'per_unit' && units.length > 0) return 'per_unit';
  if (groups.length > 0 && units.length === 0) return 'grouped';
  if (units.length > 0) return 'per_unit';
  if (promptSetupType in PROMPT_SETUP_TYPE_TO_INTERNAL) {
    return PROMPT_SETUP_TYPE_TO_INTERNAL[promptSetupType];
  }
  return 'fixed';
}

// ── Formatting helpers ────────────────────────────────────────────────

const TITLE_OVERRIDES: Record<string, string> = {
  dre_file_number: 'DRE file number',
  total_units: 'Total units',
  source_pages: 'Source pages',
  source_page: 'Source page',
  requires_dre_for_future_years: 'Requires DRE for future years',
  document_title: 'Document title',
  document_date: 'Document date',
  association_name: 'Association name',
  setup_type: 'Setup type',
  display_mode: 'Display mode',
};

function humanizeKey(key: string): string {
  if (TITLE_OVERRIDES[key]) return TITLE_OVERRIDES[key];
  const spaced = key.replace(/_/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function formatConfidence(v: unknown): { label: string; tone: 'high' | 'mid' | 'low' } | null {
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return null;
  const pct = Math.round(n * 100);
  const tone = pct >= 90 ? 'high' : pct >= 70 ? 'mid' : 'low';
  return { label: `${pct}%`, tone };
}

function formatPageList(pages: unknown): number[] {
  // Accept array shape (e.g. document_metadata.source_pages) AND scalar
  // shape (e.g. group.source_page, unit.source_page, factor.source_page).
  // The wire schema uses both forms across different entity types.
  if (pages == null) return [];
  const items = Array.isArray(pages) ? pages : [pages];
  return items
    .map((p) => (typeof p === 'number' ? p : Number(p)))
    .filter((p): p is number => Number.isFinite(p));
}

function isEmpty(v: unknown): boolean {
  return (
    v === null ||
    v === undefined ||
    v === '' ||
    (Array.isArray(v) && v.length === 0)
  );
}

function formatNumber(v: unknown, decimals = 0): string {
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

// ── Visual primitives ─────────────────────────────────────────────────

function StatusPill({ kind, value }: { kind: 'job' | 'status' | 'review'; value: string }) {
  const palette: Record<string, string> = {
    queued: 'bg-amber-50 text-amber-700 ring-amber-600/20',
    running: 'bg-blue-50 text-blue-700 ring-blue-600/20',
    completed: 'bg-slate-50 text-slate-700 ring-slate-500/20',
    succeeded: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
    extraction_partial: 'bg-amber-50 text-amber-700 ring-amber-600/20',
    failed: 'bg-rose-50 text-rose-700 ring-rose-600/20',
    pending: 'bg-slate-50 text-slate-700 ring-slate-500/20',
    approved: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
    promoted: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
    rejected: 'bg-rose-50 text-rose-700 ring-rose-600/20',
  };
  const tone = palette[value] || 'bg-slate-50 text-slate-700 ring-slate-500/20';
  const prefix = kind === 'job' ? 'Job' : kind === 'status' ? 'Status' : 'Review';
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        tone,
      )}
    >
      <span className="text-[10px] uppercase tracking-wide text-current/70 opacity-70">
        {prefix}
      </span>
      <span className="font-semibold">{value}</span>
    </span>
  );
}

function SeverityBadge({ severity }: { severity: 'low' | 'medium' | 'high' | undefined }) {
  const s = severity || 'medium';
  const palette = {
    low: 'bg-slate-100 text-slate-700 ring-slate-500/20',
    medium: 'bg-amber-100 text-amber-800 ring-amber-600/30',
    high: 'bg-rose-100 text-rose-800 ring-rose-600/30',
  } as const;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ring-1 ring-inset',
        palette[s],
      )}
      aria-label={`severity: ${s}`}
    >
      <span aria-hidden>●</span>
      {s}
    </span>
  );
}

function ConfidenceBadge({ value }: { value: unknown }) {
  const fc = formatConfidence(value);
  if (!fc) return <span className="text-slate-400">—</span>;
  const palette = {
    high: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
    mid: 'bg-amber-50 text-amber-700 ring-amber-600/30',
    low: 'bg-rose-50 text-rose-700 ring-rose-600/30',
  } as const;
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums ring-1 ring-inset',
        palette[fc.tone],
      )}
    >
      {fc.label}
    </span>
  );
}

function ManualEntryBadge() {
  return (
    <span
      title="Operator-entered — no Gemini extraction or source-page citation exists for this run"
      className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 ring-1 ring-inset ring-slate-300/60"
    >
      Manually entered
    </span>
  );
}

// Manual entries have confidence=1.0 by construction (operator-asserted,
// not extracted) — showing a "100%" badge would look like a fabricated
// AI confidence score, so swap in a plain "Manually entered" pill instead.
function ManualOrConfidenceBadge({ isManual, value }: { isManual: boolean; value: unknown }) {
  if (isManual) return <ManualEntryBadge />;
  return <ConfidenceBadge value={value} />;
}

function ChipList({
  items,
  emptyLabel,
}: {
  items: string[];
  emptyLabel?: string;
}) {
  if (!items || items.length === 0) {
    return emptyLabel ? (
      <span className="text-slate-400">{emptyLabel}</span>
    ) : null;
  }
  return (
    <span className="inline-flex flex-wrap gap-1">
      {items.map((item, i) => (
        <span
          key={`${item}-${i}`}
          className="inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-700 ring-1 ring-inset ring-slate-300/60"
          title={item}
        >
          {item}
        </span>
      ))}
    </span>
  );
}

function PageChips({ pages }: { pages: unknown }) {
  const list = formatPageList(pages);
  const jumpToPage = useContext(PdfJumpContext);
  if (list.length === 0) return <span className="text-slate-400">—</span>;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {list.map((p) =>
        jumpToPage ? (
          <button
            key={p}
            type="button"
            onClick={() => jumpToPage(p)}
            title={`Jump to page ${p} in the source PDF`}
            className="inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-inset ring-slate-300/60 hover:bg-slate-200 hover:ring-slate-400"
          >
            Page {p}
          </button>
        ) : (
          <span
            key={p}
            className="inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-inset ring-slate-300/60"
          >
            Page {p}
          </span>
        ),
      )}
    </span>
  );
}

function MonoPill({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-700 ring-1 ring-inset ring-slate-300/60">
      {children}
    </span>
  );
}

type KVRow = { key: string; value: unknown };

function KeyValueGrid({ rows, isManual = false }: { rows: KVRow[]; isManual?: boolean }) {
  if (rows.length === 0) {
    return <p className="text-sm text-slate-500">No fields extracted.</p>;
  }
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
      {rows.map(({ key, value }) => (
        <div key={key} className="contents">
          <dt className="text-slate-500">{humanizeKey(key)}</dt>
          <dd className="text-slate-900">{renderKVValue(key, value, isManual)}</dd>
        </div>
      ))}
    </dl>
  );
}

const CURRENCY_KEY_PATTERNS = /(contribution|balance|amount|budget|revenue|cost|expense)/i;
const PERCENT_KEY_PATTERNS = /(assumption|_rate|_percent|inflation|interest)/i;

function renderKVValue(key: string, value: unknown, isManual = false): React.ReactNode {
  if (key === 'confidence') return <ManualOrConfidenceBadge isManual={isManual} value={value} />;
  if (key === 'source_pages' || key === 'source_page') {
    const arr = Array.isArray(value) ? value : value != null ? [value] : [];
    return <PageChips pages={arr} />;
  }
  if (key === 'setup_type' || key === 'display_mode' || key === 'assessment_setup_type') {
    return <MonoPill>{String(value)}</MonoPill>;
  }
  if (key === 'notes') {
    return (
      <p className="whitespace-pre-wrap text-slate-800">
        {String(value)}
      </p>
    );
  }
  if (
    key === 'required_manual_fields' ||
    key === 'required_budget_line_mappings'
  ) {
    const items = Array.isArray(value)
      ? value.map((v) => String(v))
      : [];
    return <ChipList items={items} emptyLabel="(none)" />;
  }
  if (typeof value === 'boolean') {
    return (
      <span
        className={cn(
          'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ring-inset',
          value
            ? 'bg-emerald-50 text-emerald-700 ring-emerald-600/20'
            : 'bg-slate-50 text-slate-600 ring-slate-500/20',
        )}
      >
        {value ? 'Yes' : 'No'}
      </span>
    );
  }
  if (typeof value === 'object' && value !== null) {
    return <span className="font-mono text-xs text-slate-600">{JSON.stringify(value)}</span>;
  }
  // Numeric formatting by key conventions
  const numeric = Number(value);
  if (Number.isFinite(numeric)) {
    if (CURRENCY_KEY_PATTERNS.test(key)) {
      return <span className="tabular-nums">{formatCurrency(numeric)}</span>;
    }
    if (PERCENT_KEY_PATTERNS.test(key)) {
      // Reserve assumptions are printed as whole-number percents (e.g. 3 = 3%);
      // render with up to 2 decimals to handle 1.5, 3.25, etc.
      const decimals = numeric % 1 === 0 ? 0 : 2;
      return (
        <span className="tabular-nums">
          {formatNumber(numeric, decimals)}%
        </span>
      );
    }
    if (key === 'total_units' || /_count$/.test(key)) {
      return <span className="tabular-nums">{formatNumber(numeric)}</span>;
    }
  }
  return <span>{String(value)}</span>;
}

// ── Pagination ────────────────────────────────────────────────────────

function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (next: number) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) return null;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);

  // Compute a windowed page range: always show first, last, current ±1, with ellipses.
  const pages: Array<number | 'gap'> = [];
  const window = new Set<number>([1, pageCount, page, page - 1, page + 1]);
  for (let i = 1; i <= pageCount; i++) {
    if (window.has(i)) pages.push(i);
  }
  const dedup: Array<number | 'gap'> = [];
  for (let i = 0; i < pages.length; i++) {
    const cur = pages[i] as number;
    const prev = i === 0 ? null : (pages[i - 1] as number);
    if (prev !== null && cur - prev > 1) dedup.push('gap');
    dedup.push(cur);
  }

  return (
    <nav
      aria-label="Units pagination"
      className="mt-3 flex items-center justify-between gap-2 text-xs"
    >
      <p className="text-slate-500 tabular-nums">
        Showing <span className="font-semibold text-slate-700">{from}</span>–
        <span className="font-semibold text-slate-700">{to}</span> of{' '}
        <span className="font-semibold text-slate-700">{total}</span>
      </p>
      <div className="inline-flex items-center gap-1">
        <button
          type="button"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          className="inline-flex h-7 items-center rounded border border-slate-200 px-2 text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          ← Prev
        </button>
        {dedup.map((p, i) =>
          p === 'gap' ? (
            <span key={`gap-${i}`} className="px-1 text-slate-400">
              …
            </span>
          ) : (
            <button
              key={p}
              type="button"
              aria-label={`Page ${p}`}
              aria-current={p === page ? 'page' : undefined}
              onClick={() => onPageChange(p)}
              className={cn(
                'inline-flex h-7 min-w-[28px] items-center justify-center rounded border px-2 tabular-nums',
                p === page
                  ? 'border-slate-900 bg-slate-900 text-white'
                  : 'border-slate-200 text-slate-700 hover:bg-slate-50',
              )}
            >
              {p}
            </button>
          ),
        )}
        <button
          type="button"
          aria-label="Next page"
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
          className="inline-flex h-7 items-center rounded border border-slate-200 px-2 text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Next →
        </button>
      </div>
    </nav>
  );
}

// ── Math validation pill (icon + color, not color-only) ───────────────

function MathCheckStatus({ status }: { status: string | undefined }) {
  const map: Record<string, { glyph: string; cls: string; label: string }> = {
    pass: { glyph: '✓', cls: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20', label: 'pass' },
    fail: { glyph: '✕', cls: 'bg-rose-50 text-rose-700 ring-rose-600/30', label: 'fail' },
    warning: { glyph: '!', cls: 'bg-amber-50 text-amber-700 ring-amber-600/30', label: 'warning' },
    not_applicable: { glyph: '–', cls: 'bg-slate-50 text-slate-600 ring-slate-500/20', label: 'n/a' },
  };
  const m = map[status || ''] || map.not_applicable;
  return (
    <span
      className={cn(
        'inline-flex w-20 items-center justify-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ring-1 ring-inset',
        m.cls,
      )}
    >
      <span aria-hidden className="font-mono">
        {m.glyph}
      </span>
      {m.label}
    </span>
  );
}

// ── Main component ────────────────────────────────────────────────────

export function DREReviewWorkbench({ hoaId, runId }: Props) {
  const [detail, setDetail] = useState<DREExtractionRunDetail | null>(null);
  const [edits, setEdits] = useState<DREReviewEdit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [chosenSetupType, setChosenSetupType] =
    useState<'fixed' | 'grouped' | 'per_unit'>('fixed');
  const [approving, setApproving] = useState(false);
  const [repromoting, setRepromoting] = useState(false);
  const [repromoteResult, setRepromoteResult] = useState<ReopenRepromoteResponse | null>(null);

  // "Compare with PDF" full-screen split view (add-dre-review-pdf-compare-view).
  const [isCompareOpen, setIsCompareOpen] = useState(false);
  const [targetPage, setTargetPage] = useState<number | undefined>(undefined);
  const jumpToPage = useCallback((page: number) => {
    setTargetPage(page);
    setIsCompareOpen(true);
  }, []);

  // Units table pagination
  const UNITS_PAGE_SIZE = 10;
  const [unitsPage, setUnitsPage] = useState(1);

  // Row-level disclosure state. Keys: `pool_key` (string) for the
  // allocation_pools detail panel; `unit_number` for the per-unit
  // factor sub-table; a single boolean for the page-inventory
  // diagnostic at the bottom of the workbench.
  const [expandedPoolKeys, setExpandedPoolKeys] = useState<Set<string>>(
    () => new Set(),
  );
  const [expandedUnitNumbers, setExpandedUnitNumbers] = useState<Set<string>>(
    () => new Set(),
  );
  const [showPageInventory, setShowPageInventory] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [d, history] = await Promise.all([
        getExtractionRun(hoaId, runId),
        listReviewEdits(hoaId, runId),
      ]);
      setDetail(d);
      setEdits(history);

      setChosenSetupType(suggestedInternalSetupType(d.parsed_json));
      setError(null);
    } catch (exc) {
      setError(describeApiError(exc));
    } finally {
      setLoading(false);
    }
  }, [hoaId, runId]);

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!detail || !ACTIVE_JOB_STATUSES.has(detail.job_status)) return;
    const timer = setInterval(() => {
      void refresh();
    }, EXTRACTION_REFRESH_MS);
    return () => clearInterval(timer);
  }, [detail, refresh]);

  const isCCR = detail?.document_type === 'ccr';
  const isManual = detail?.model_name === 'manual';

  async function onSaveEdit(
    fieldPath: string,
    oldValue: unknown,
    newValue: unknown,
  ) {
    if (oldValue === newValue) return;
    const reason = prompt(`Reason for change to ${fieldPath}?`, '') || undefined;
    try {
      await recordReviewEdit(hoaId, runId, {
        field_path: fieldPath,
        old_value: oldValue,
        new_value: newValue,
        reason,
      });
      void refresh();
    } catch (exc) {
      setError(describeApiError(exc));
    }
  }

  async function onApprove() {
    if (!detail) return;
    if (
      !confirm(
        `Approve extraction with setup_type=${chosenSetupType}? ` +
          `This creates a new AssessmentSetup and supersedes any prior approved one.`,
      )
    ) {
      return;
    }
    setApproving(true);
    setError(null);
    try {
      if (isCCR) {
        await approveCCRRun(hoaId, runId, chosenSetupType);
      } else {
        await approveExtractionRun(hoaId, runId, chosenSetupType);
      }
      void refresh();
    } catch (exc) {
      setError(describeApiError(exc));
    } finally {
      setApproving(false);
    }
  }

  async function onRepromote() {
    if (!detail) return;
    if (
      !confirm(
        `Re-promote with corrections using setup_type=${chosenSetupType}? ` +
          `The current AssessmentSetup will be superseded by a fresh one built from ` +
          `the original extraction plus every review edit on file now.`,
      )
    ) {
      return;
    }
    setRepromoting(true);
    setError(null);
    setRepromoteResult(null);
    try {
      const resp = isCCR
        ? await reopenAndRepromoteCCRRun(hoaId, runId, chosenSetupType)
        : await reopenAndRepromoteExtractionRun(hoaId, runId, chosenSetupType);
      setRepromoteResult(resp);
      void refresh();
    } catch (exc) {
      setError(describeApiError(exc));
    } finally {
      setRepromoting(false);
    }
  }

  // field_path -> count of dre_review_edits rows for that path, so any
  // edited field can show "N edits" without hiding the superseded ones —
  // the full chronological list is still in the Edit history section below.
  const editCountByPath = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const e of edits) counts[e.field_path] = (counts[e.field_path] || 0) + 1;
    return counts;
  }, [edits]);

  const parsed = detail?.parsed_json || {};
  const docMeta = (parsed.document_metadata || {}) as Record<string, unknown>;
  const setup = (parsed.assessment_setup || {}) as Record<string, unknown>;
  const pools = (parsed.allocation_pools || []) as Array<Record<string, unknown>>;
  const groups = (
    (parsed.unit_structure as { groups?: unknown[] })?.groups || []
  ) as Array<Record<string, unknown>>;
  const units = (
    (parsed.unit_structure as { units?: unknown[] })?.units || []
  ) as Array<Record<string, unknown>>;
  // Ownership-% form ambiguity — mirrors the backend resolver
  // (assessment_engine/percent_form.py::resolve_percent_divisor): the engine
  // blocks rendering only when the column sums to neither ~1.0 (values written
  // as decimals, 0.0347 = 3.47%) nor ~100 (values written as points, 3.47 =
  // 3.47%) AND no value exceeds 1. Surface a confirm control ONLY in that exact
  // case, so a normal HOA never sees it.
  const ownershipForm = String(
    (parsed.unit_structure as { ownership_percent_form?: unknown })
      ?.ownership_percent_form || 'unknown',
  );
  const ownershipValues = units
    .map((u) => Number((u as { ownership_percent?: unknown }).ownership_percent))
    .filter((v) => Number.isFinite(v));
  const ownershipSum = ownershipValues.reduce((a, b) => a + b, 0);
  const ownershipFormNeedsDecision =
    ownershipForm !== 'fraction' &&
    ownershipForm !== 'points' &&
    ownershipValues.length > 0 &&
    !(ownershipSum >= 0.98 && ownershipSum <= 1.02) && // decimals band
    !(ownershipSum >= 98 && ownershipSum <= 102) && // points band
    !ownershipValues.some((v) => v > 1.02) && // any value > 1 ⇒ points
    ownershipSum >= 0.98; // sum < 0.98 ⇒ decimals (sub-scope)
  const ownershipFormEditCount =
    editCountByPath['unit_structure.ownership_percent_form'] || 0;
  const formulas = (parsed.formulas || []) as Array<Record<string, unknown>>;
  const reserveSetup = (parsed.reserve_setup ?? null) as Record<string, unknown> | null;
  const recommendedSetup = (parsed.recommended_saved_setup ?? null) as
    | Record<string, unknown>
    | null;
  const pageInventory = (parsed.page_inventory || []) as Array<{
    page_number?: number;
    page_type?: string;
    confidence?: number | null;
    notes?: string | null;
  }>;
  const warnings = detail?.validation_warnings || [];
  const validationChecks = (parsed.validation_checks || []) as Array<{
    check_name?: string;
    status?: string;
    details?: string;
  }>;
  const humanReviewQuestions = (parsed.human_review_questions || []) as Array<{
    question?: string;
    reason?: string;
    severity?: 'low' | 'medium' | 'high';
    source_pages?: number[];
  }>;
  const lowConfidenceFlags = detail?.low_confidence_flags || [];

  // Clamp pagination if the underlying data shrinks across refreshes.
  useEffect(() => {
    const pageCount = Math.max(1, Math.ceil(units.length / UNITS_PAGE_SIZE));
    if (unitsPage > pageCount) setUnitsPage(1);
  }, [units.length, unitsPage]);

  // Drop unit-row expansions when the operator paginates to a different
  // page, so an expanded unit on page 1 doesn't leak open behavior to
  // a different unit on page 2.
  useEffect(() => {
    setExpandedUnitNumbers(new Set());
  }, [unitsPage]);

  function togglePool(poolKey: string) {
    setExpandedPoolKeys((current) => {
      const next = new Set(current);
      if (next.has(poolKey)) next.delete(poolKey);
      else next.add(poolKey);
      return next;
    });
  }

  function toggleUnit(unitNumber: string) {
    setExpandedUnitNumbers((current) => {
      const next = new Set(current);
      if (next.has(unitNumber)) next.delete(unitNumber);
      else next.add(unitNumber);
      return next;
    });
  }

  const pagedUnits = useMemo(() => {
    const start = (unitsPage - 1) * UNITS_PAGE_SIZE;
    return units.slice(start, start + UNITS_PAGE_SIZE);
  }, [units, unitsPage]);

  if (loading) {
    return <div className="p-4 text-slate-500">Loading extraction run…</div>;
  }
  if (!detail) {
    return <div className="p-4 text-rose-600">{error || 'Run not found'}</div>;
  }

  const jobStillRunning = ACTIVE_JOB_STATUSES.has(detail.job_status);
  if (jobStillRunning) {
    return (
      <section className="space-y-4 p-4">
        <header className="space-y-1.5">
          <h1 className="text-xl font-semibold text-slate-900">DRE Review Workbench</h1>
          <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
            <span>HOA {hoaId}</span>
            <span className="text-slate-300">·</span>
            <span>Run #{runId}</span>
            <StatusPill kind="job" value={detail.job_status} />
            <StatusPill kind="review" value={detail.review_status} />
          </div>
        </header>
        <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">
          Extraction still running. This page refreshes automatically and will show the
          extracted setup when the run finishes.
        </div>
        {error ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            {error}
          </div>
        ) : null}
      </section>
    );
  }

  if (detail.job_status === 'failed' && !detail.parsed_json) {
    return (
      <section className="space-y-4 p-4">
        <header className="space-y-1.5">
          <h1 className="text-xl font-semibold text-slate-900">DRE Review Workbench</h1>
          <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
            <span>HOA {hoaId}</span>
            <span className="text-slate-300">·</span>
            <span>Run #{runId}</span>
            <StatusPill kind="job" value={detail.job_status} />
            <StatusPill kind="review" value={detail.review_status} />
          </div>
        </header>
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">
          {detail.error_message || 'Extraction failed before review data was available.'}
        </div>
      </section>
    );
  }

  const alreadyPromoted = detail.review_status === 'promoted';
  const reviewReady = detail.job_status === 'completed';

  // Curate metadata / setup keys in display order, filter empty values.
  const METADATA_KEYS = [
    'association_name',
    'document_title',
    'dre_file_number',
    'document_date',
    'preparer',
    'location',
    'total_units',
    'confidence',
    'source_pages',
  ];
  const SETUP_KEYS = [
    'setup_type',
    'display_mode',
    'summary',
    'requires_dre_for_future_years',
    'confidence',
    'source_pages',
  ];
  const metadataRows: KVRow[] = METADATA_KEYS
    .filter((k) => !isEmpty(docMeta[k]))
    .map((k) => ({ key: k, value: docMeta[k] }));
  const setupRows: KVRow[] = SETUP_KEYS
    .filter((k) => !isEmpty(setup[k]))
    .map((k) => ({ key: k, value: setup[k] }));

  const RESERVE_KEYS = [
    'reserve_contribution',
    'reserve_beginning_balance',
    'inflation_assumption',
    'interest_assumption',
    'allocation_method',
    'confidence',
    'source_pages',
  ];
  const reserveRows: KVRow[] = reserveSetup
    ? RESERVE_KEYS
        .filter((k) => !isEmpty(reserveSetup[k]))
        .map((k) => ({ key: k, value: reserveSetup[k] }))
    : [];

  const RECOMMENDED_KEYS = [
    'assessment_setup_type',
    'display_mode',
    'required_manual_fields',
    'required_budget_line_mappings',
    'notes',
  ];
  const recommendedRows: KVRow[] = recommendedSetup
    ? RECOMMENDED_KEYS
        // Keep list-of-strings fields even when empty so the operator
        // sees an explicit "(none)" instead of guessing.
        .filter((k) =>
          k === 'required_manual_fields' ||
            k === 'required_budget_line_mappings'
            ? recommendedSetup[k] !== undefined
            : !isEmpty(recommendedSetup[k]),
        )
        .map((k) => ({ key: k, value: recommendedSetup[k] }))
    : [];

  const mainContent = (
    <section className="space-y-6 p-4">
      {/* ── Header ────────────────────────────────────────────────── */}
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1.5">
          <h1 className="text-xl font-semibold text-slate-900">DRE Review Workbench</h1>
          <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
            <span>HOA {hoaId}</span>
            <span className="text-slate-300">·</span>
            <span>Run #{runId}</span>
            <StatusPill kind="job" value={detail.job_status} />
            <StatusPill kind="status" value={detail.status} />
            <StatusPill kind="review" value={detail.review_status} />
          </div>
          {detail.model_name && (
            <p className="text-xs text-slate-400">
              {isManual ? (
                <ManualEntryBadge />
              ) : (
                <>
                  {detail.model_name} · prompt v{detail.prompt_version}
                </>
              )}
              {isCCR && <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-500">CC&R</span>}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!isCompareOpen && (
            <button
              type="button"
              onClick={() => setIsCompareOpen(true)}
              className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
            >
              Compare with PDF
            </button>
          )}
          <select
            className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 shadow-sm focus:outline-none focus:ring-2 focus:ring-slate-300 disabled:bg-slate-50"
            value={chosenSetupType}
            disabled={approving || repromoting || !reviewReady}
            onChange={(e) =>
              setChosenSetupType(
                e.target.value as 'fixed' | 'grouped' | 'per_unit',
              )
            }
          >
            {(Object.keys(SETUP_TYPE_LABELS) as Array<'fixed' | 'grouped' | 'per_unit'>).map(
              (k) => (
                <option key={k} value={k}>
                  {SETUP_TYPE_LABELS[k]}
                </option>
              ),
            )}
          </select>
          <button
            type="button"
            onClick={onApprove}
            disabled={alreadyPromoted || approving || !reviewReady}
            className={cn(
              'inline-flex items-center gap-2 rounded-md px-4 py-1.5 text-sm font-semibold shadow-sm transition',
              alreadyPromoted
                ? 'bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-600/20'
                : approving
                  ? 'bg-emerald-500 text-white opacity-75'
                  : 'bg-emerald-600 text-white hover:bg-emerald-700',
              'disabled:cursor-not-allowed',
            )}
          >
            {alreadyPromoted
              ? `✓ Promoted (setup #${detail.promoted_setup_id})`
              : approving
                ? 'Approving…'
                : 'Approve → Promote to AssessmentSetup'}
          </button>
          {alreadyPromoted && (
            <button
              type="button"
              onClick={onRepromote}
              disabled={repromoting || !reviewReady}
              title="Re-apply the original extraction plus every review edit on file now, superseding the current AssessmentSetup"
              className={cn(
                'inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm font-semibold shadow-sm transition',
                repromoting
                  ? 'border-indigo-200 bg-indigo-50 text-indigo-400'
                  : 'border-indigo-200 bg-white text-indigo-700 hover:bg-indigo-50',
                'disabled:cursor-not-allowed',
              )}
            >
              {repromoting ? 'Re-promoting…' : '↻ Re-promote with corrections'}
            </button>
          )}
        </div>
      </header>

      {error && (
        <div
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
        >
          {error}
        </div>
      )}

      {repromoteResult && (
        <div className="rounded-md border border-indigo-200 bg-indigo-50 p-3 text-sm text-indigo-900">
          <p>
            Re-promoted into setup #{repromoteResult.promoted_setup_id} (superseded setup
            #{repromoteResult.superseded_setup_id ?? '—'}).
          </p>
          {repromoteResult.affected_draft_package_ids.length > 0 && (
            <p className="mt-1 font-medium">
              {repromoteResult.affected_draft_package_ids.length} draft annual package
              {repromoteResult.affected_draft_package_ids.length === 1 ? '' : 's'} (id
              {repromoteResult.affected_draft_package_ids.length === 1 ? '' : 's'}:{' '}
              {repromoteResult.affected_draft_package_ids.join(', ')}) still reference the
              superseded setup and need regeneration.
            </p>
          )}
        </div>
      )}

      {/* ── Operator review required ──────────────────────────────── */}
      {(humanReviewQuestions.length > 0 ||
        lowConfidenceFlags.length > 0 ||
        warnings.length > 0) && (
        <div className="rounded-md border border-amber-200 bg-amber-50/60">
          <div className="flex items-center justify-between border-b border-amber-200/80 px-3 py-2">
            <h3 className="text-sm font-semibold text-amber-900">
              Operator review required
            </h3>
            <span className="text-xs text-amber-700">
              {humanReviewQuestions.length} question
              {humanReviewQuestions.length === 1 ? '' : 's'}
              {lowConfidenceFlags.length > 0 &&
                ` · ${lowConfidenceFlags.length} low-confidence`}
              {warnings.length > 0 && ` · ${warnings.length} math warning${warnings.length === 1 ? '' : 's'}`}
            </span>
          </div>
          <ul className="divide-y divide-amber-200/70 text-sm">
            {humanReviewQuestions.map((q, i) => {
              const borderTone =
                q.severity === 'high'
                  ? 'border-l-rose-500'
                  : q.severity === 'medium'
                    ? 'border-l-amber-500'
                    : 'border-l-slate-300';
              return (
                <li
                  key={i}
                  className={cn(
                    'flex flex-wrap items-start gap-3 border-l-4 px-3 py-2',
                    borderTone,
                  )}
                >
                  <SeverityBadge severity={q.severity} />
                  <div className="flex-1 text-slate-800">
                    {q.question}
                    {q.source_pages && q.source_pages.length > 0 && (
                      <span className="ml-2 inline-flex items-center gap-1">
                        <PageChips pages={q.source_pages} />
                      </span>
                    )}
                    {q.reason && (
                      <p className="mt-0.5 text-xs text-slate-500">{q.reason}</p>
                    )}
                  </div>
                </li>
              );
            })}
            {lowConfidenceFlags.map((f, i) => (
              <li
                key={`lcf-${i}`}
                className="flex items-center gap-3 border-l-4 border-l-slate-300 px-3 py-2"
              >
                <ConfidenceBadge value={f.confidence} />
                <span className="font-mono text-xs text-slate-600">{f.path}</span>
              </li>
            ))}
            {warnings.map((w, i) => (
              <li
                key={`warn-${i}`}
                className="border-l-4 border-l-amber-500 px-3 py-2 text-slate-800"
              >
                <span className="mr-2 inline-flex items-center rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-amber-800 ring-1 ring-inset ring-amber-600/30">
                  Math
                </span>
                {w}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Document metadata ─────────────────────────────────────── */}
      <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-slate-900">Document metadata</h2>
        <KeyValueGrid rows={metadataRows} isManual={isManual} />
      </section>

      {/* ── Assessment setup ──────────────────────────────────────── */}
      <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-slate-900">Assessment setup</h2>
        <KeyValueGrid rows={setupRows} isManual={isManual} />
      </section>

      {/* ── Allocation pools ──────────────────────────────────────── */}
      <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-slate-900">
          Allocation pools{' '}
          <span className="font-normal text-slate-500">({pools.length})</span>
        </h2>
        {pools.length === 0 ? (
          <p className="text-sm text-slate-500">No pools detected.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="py-2 pr-2 font-medium">Parent</th>
                  <th className="py-2 pr-4 font-medium">Key</th>
                  <th className="py-2 pr-4 font-medium">Name</th>
                  <th className="py-2 pr-4 font-medium">Method</th>
                  <th className="py-2 pr-4 font-medium">Scope</th>
                  <th className="py-2 pr-4 text-right font-medium">Denominator</th>
                  <th className="py-2 pr-4 text-right font-medium">Annual</th>
                  <th className="py-2 pr-4 text-center font-medium">Variable</th>
                  <th
                    className="py-2 pr-4 text-center font-medium"
                    title="Mark this pool a special assessment: it is billed separately (one-time), allocated by its basis, and shown in Settings → Special Assessments. Never inferred from the pool name."
                  >
                    Special?
                  </th>
                  <th className="py-2 pr-4 text-right font-medium">Confidence</th>
                  <th className="py-2 text-right font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {pools.map((pool, i) => {
                  const poolKey = String(pool.pool_key);
                  const parent = String(pool.parent_pool_key || '');
                  const expanded = expandedPoolKeys.has(poolKey);
                  const included = Array.isArray(pool.included_budget_lines)
                    ? (pool.included_budget_lines as unknown[]).map((x) =>
                        String(x),
                      )
                    : [];
                  const excluded = Array.isArray(pool.excluded_budget_lines)
                    ? (pool.excluded_budget_lines as unknown[]).map((x) =>
                        String(x),
                      )
                    : [];
                  return (
                    <Fragment key={i}>
                      <tr className="border-b border-slate-100 last:border-b-0">
                        <td className="py-2 pr-2">
                          {parent ? (
                            <span
                              className="inline-flex items-center gap-0.5 rounded bg-indigo-50 px-1.5 py-0.5 text-[11px] font-medium text-indigo-700 ring-1 ring-inset ring-indigo-600/20"
                              title={`Component of ${parent}`}
                            >
                              <span aria-hidden>↳</span>
                              <span className="font-mono">{parent}</span>
                            </span>
                          ) : (
                            <span className="text-slate-300">—</span>
                          )}
                        </td>
                        <td className="py-2 pr-4">
                          <MonoPill>{poolKey}</MonoPill>
                        </td>
                        <td className="py-2 pr-4 text-slate-800">
                          <EditableValue
                            value={pool.pool_name || ''}
                            fieldPath={`allocation_pools[${i}].pool_name`}
                            onSave={onSaveEdit}
                            disabled={!reviewReady}
                            editCount={editCountByPath[`allocation_pools[${i}].pool_name`]}
                          />
                        </td>
                        <td className="py-2 pr-4">
                          <EditableValue
                            value={pool.allocation_method || ''}
                            fieldPath={`allocation_pools[${i}].allocation_method`}
                            onSave={onSaveEdit}
                            disabled={!reviewReady}
                            editCount={editCountByPath[`allocation_pools[${i}].allocation_method`]}
                            kind="select"
                            options={ALLOCATION_METHOD_OPTIONS}
                            display={<MonoPill>{String(pool.allocation_method || '')}</MonoPill>}
                          />
                        </td>
                        <td className="py-2 pr-4 font-mono text-xs text-slate-600">
                          <EditableValue
                            value={pool.recipient_scope || ''}
                            fieldPath={`allocation_pools[${i}].recipient_scope`}
                            onSave={onSaveEdit}
                            disabled={!reviewReady}
                            editCount={editCountByPath[`allocation_pools[${i}].recipient_scope`]}
                            kind="select"
                            options={RECIPIENT_SCOPE_OPTIONS}
                          />
                        </td>
                        <td className="py-2 pr-4 text-right tabular-nums text-slate-800">
                          <EditableValue
                            value={pool.denominator_value ?? null}
                            fieldPath={`allocation_pools[${i}].denominator_value`}
                            onSave={onSaveEdit}
                            disabled={!reviewReady}
                            editCount={editCountByPath[`allocation_pools[${i}].denominator_value`]}
                            kind="number"
                            display={
                              pool.denominator_value !== undefined && pool.denominator_value !== null
                                ? formatNumber(pool.denominator_value)
                                : undefined
                            }
                          />
                        </td>
                        <td className="py-2 pr-4 text-right tabular-nums font-medium text-slate-900">
                          <EditableValue
                            value={pool.annual_amount ?? null}
                            fieldPath={`allocation_pools[${i}].annual_amount`}
                            onSave={onSaveEdit}
                            disabled={!reviewReady}
                            editCount={editCountByPath[`allocation_pools[${i}].annual_amount`]}
                            kind="number"
                            display={
                              pool.annual_amount !== undefined &&
                              pool.annual_amount !== null
                                ? formatCurrency(Number(pool.annual_amount))
                                : undefined
                            }
                          />
                        </td>
                        <td className="py-2 pr-4 text-center">
                          <input
                            type="checkbox"
                            aria-label={`variable flag for pool ${poolKey}`}
                            defaultChecked={Boolean(pool.variable_flag)}
                            disabled={!reviewReady}
                            onChange={(e) =>
                              onSaveEdit(
                                `allocation_pools[${i}].variable_flag`,
                                Boolean(pool.variable_flag),
                                e.target.checked,
                              )
                            }
                          />
                          {Boolean(editCountByPath[`allocation_pools[${i}].variable_flag`]) && (
                            <span
                              title={`${editCountByPath[`allocation_pools[${i}].variable_flag`]} edit(s) — see Edit history below`}
                              className="ml-1 inline-flex items-center rounded-full bg-indigo-50 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700 ring-1 ring-inset ring-indigo-600/20"
                            >
                              {editCountByPath[`allocation_pools[${i}].variable_flag`]}✎
                            </span>
                          )}
                        </td>
                        <td className="py-2 pr-4 text-center">
                          <input
                            type="checkbox"
                            aria-label={`special assessment flag for pool ${poolKey}`}
                            defaultChecked={pool.pool_kind === 'separately_billed_special_assessment'}
                            disabled={!reviewReady}
                            onChange={(e) =>
                              onSaveEdit(
                                `allocation_pools[${i}].pool_kind`,
                                pool.pool_kind || '',
                                e.target.checked ? 'separately_billed_special_assessment' : '',
                              )
                            }
                          />
                          {Boolean(editCountByPath[`allocation_pools[${i}].pool_kind`]) && (
                            <span
                              title={`${editCountByPath[`allocation_pools[${i}].pool_kind`]} edit(s) — see Edit history below`}
                              className="ml-1 inline-flex items-center rounded-full bg-indigo-50 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700 ring-1 ring-inset ring-indigo-600/20"
                            >
                              {editCountByPath[`allocation_pools[${i}].pool_kind`]}✎
                            </span>
                          )}
                        </td>
                        <td className="py-2 pr-4 text-right">
                          <ManualOrConfidenceBadge isManual={isManual} value={pool.confidence} />
                        </td>
                        <td className="py-2 text-right">
                          <button
                            type="button"
                            onClick={() => togglePool(poolKey)}
                            aria-expanded={expanded}
                            className="inline-flex items-center gap-1 rounded border border-slate-200 px-2 py-0.5 text-xs text-slate-700 hover:bg-slate-50"
                          >
                            {expanded ? 'Hide' : 'Details'}
                            <span aria-hidden>{expanded ? '▴' : '▾'}</span>
                          </button>
                        </td>
                      </tr>
                      {expanded && (
                        <tr className="border-b border-slate-100 bg-slate-50/50">
                          <td colSpan={10} className="px-3 py-3">
                            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-xs">
                              <dt className="text-slate-500">Monthly</dt>
                              <dd className="text-slate-900 tabular-nums">
                                <EditableValue
                                  value={pool.monthly_amount ?? null}
                                  fieldPath={`allocation_pools[${i}].monthly_amount`}
                                  onSave={onSaveEdit}
                                  disabled={!reviewReady}
                                  editCount={editCountByPath[`allocation_pools[${i}].monthly_amount`]}
                                  kind="number"
                                  display={
                                    pool.monthly_amount !== undefined &&
                                    pool.monthly_amount !== null
                                      ? formatCurrency(Number(pool.monthly_amount))
                                      : undefined
                                  }
                                />
                              </dd>
                              <dt className="text-slate-500">Denominator label</dt>
                              <dd className="text-slate-900">
                                <EditableValue
                                  value={pool.denominator_label || ''}
                                  fieldPath={`allocation_pools[${i}].denominator_label`}
                                  onSave={onSaveEdit}
                                  disabled={!reviewReady}
                                  editCount={editCountByPath[`allocation_pools[${i}].denominator_label`]}
                                  display={
                                    pool.denominator_label ? (
                                      <span className="font-mono">{String(pool.denominator_label)}</span>
                                    ) : undefined
                                  }
                                />
                              </dd>
                              <dt className="text-slate-500">Denominator source</dt>
                              <dd>
                                <EditableValue
                                  value={pool.denominator_source || ''}
                                  fieldPath={`allocation_pools[${i}].denominator_source`}
                                  onSave={onSaveEdit}
                                  disabled={!reviewReady}
                                  editCount={editCountByPath[`allocation_pools[${i}].denominator_source`]}
                                  kind="select"
                                  options={DENOMINATOR_SOURCE_OPTIONS}
                                  display={
                                    pool.denominator_source ? (
                                      <MonoPill>{String(pool.denominator_source)}</MonoPill>
                                    ) : undefined
                                  }
                                />
                              </dd>
                              <dt className="text-slate-500">Source pages</dt>
                              <dd>
                                <PageChips pages={pool.source_pages} />
                              </dd>
                              <dt className="text-slate-500">
                                Included budget lines{' '}
                                <span className="text-slate-400">
                                  ({included.length})
                                </span>
                              </dt>
                              <dd>
                                <ChipList items={included} emptyLabel="(none)" />
                              </dd>
                              {excluded.length > 0 && (
                                <>
                                  <dt className="text-slate-500">
                                    Excluded budget lines{' '}
                                    <span className="text-slate-400">
                                      ({excluded.length})
                                    </span>
                                  </dt>
                                  <dd>
                                    <ChipList items={excluded} />
                                  </dd>
                                </>
                              )}
                            </dl>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Groups ───────────────────────────────────────────────── */}
      {groups.length > 0 && (
        <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-slate-900">
            Groups <span className="font-normal text-slate-500">({groups.length})</span>
          </h2>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="py-2 pr-4 font-medium">ID</th>
                  <th className="py-2 pr-4 font-medium">Label</th>
                  <th className="py-2 pr-4 text-right font-medium">Unit count</th>
                  <th className="py-2 pr-4 text-right font-medium">Avg sq ft</th>
                  <th className="py-2 pr-4 text-right font-medium">Ownership %</th>
                  <th className="py-2 pr-4 text-right font-medium">Factor</th>
                  <th className="py-2 pr-4 font-medium">Source page</th>
                  <th className="py-2 text-right font-medium">Confidence</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((g, i) => (
                  <tr
                    key={i}
                    className="border-b border-slate-100 last:border-b-0"
                  >
                    <td className="py-2 pr-4 font-mono text-xs text-slate-700">
                      {String(g.group_id || i)}
                    </td>
                    <td className="py-2 pr-4 text-slate-800">
                      <EditableValue
                        value={g.label || ''}
                        fieldPath={`unit_structure.groups[${i}].label`}
                        onSave={onSaveEdit}
                        disabled={!reviewReady}
                        editCount={editCountByPath[`unit_structure.groups[${i}].label`]}
                      />
                    </td>
                    <td className="py-2 pr-4 text-right tabular-nums">
                      <EditableValue
                        value={g.unit_count ?? null}
                        fieldPath={`unit_structure.groups[${i}].unit_count`}
                        onSave={onSaveEdit}
                        disabled={!reviewReady}
                        editCount={editCountByPath[`unit_structure.groups[${i}].unit_count`]}
                        kind="number"
                        display={g.unit_count != null ? formatNumber(g.unit_count) : undefined}
                      />
                    </td>
                    <td className="py-2 pr-4 text-right tabular-nums">
                      <EditableValue
                        value={g.average_square_feet ?? null}
                        fieldPath={`unit_structure.groups[${i}].average_square_feet`}
                        onSave={onSaveEdit}
                        disabled={!reviewReady}
                        editCount={editCountByPath[`unit_structure.groups[${i}].average_square_feet`]}
                        kind="number"
                        display={
                          g.average_square_feet != null ? formatNumber(g.average_square_feet) : undefined
                        }
                      />
                    </td>
                    <td className="py-2 pr-4 text-right tabular-nums">
                      <EditableValue
                        value={g.ownership_percent ?? null}
                        fieldPath={`unit_structure.groups[${i}].ownership_percent`}
                        onSave={onSaveEdit}
                        disabled={!reviewReady}
                        editCount={editCountByPath[`unit_structure.groups[${i}].ownership_percent`]}
                        kind="number"
                        display={
                          g.ownership_percent != null ? `${formatNumber(g.ownership_percent, 2)}%` : undefined
                        }
                      />
                    </td>
                    <td className="py-2 pr-4 text-right tabular-nums">
                      <EditableValue
                        value={g.factor ?? null}
                        fieldPath={`unit_structure.groups[${i}].factor`}
                        onSave={onSaveEdit}
                        disabled={!reviewReady}
                        editCount={editCountByPath[`unit_structure.groups[${i}].factor`]}
                        kind="number"
                        display={g.factor != null ? formatNumber(g.factor, 2) : undefined}
                      />
                    </td>
                    <td className="py-2 pr-4">
                      <PageChips pages={g.source_page} />
                    </td>
                    <td className="py-2 text-right">
                      <ManualOrConfidenceBadge isManual={isManual} value={g.confidence} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── Ownership-% form decision (only when genuinely ambiguous) ── */}
      {ownershipFormNeedsDecision && (
        <section className="rounded-md border border-amber-300 bg-amber-50 p-4 shadow-sm">
          <h2 className="text-sm font-semibold text-amber-900">
            Confirm ownership % form
          </h2>
          <p className="mt-1 text-xs leading-relaxed text-amber-800">
            These ownership percentages add up to{' '}
            <span className="font-semibold tabular-nums">
              {ownershipSum.toFixed(2)}
            </span>
            , which is neither ~1.0 (written as decimals, e.g. 0.0347 = 3.47%) nor
            ~100 (written as points, e.g. 3.47 = 3.47%). The engine will not guess,
            so confirm how to read them. This does not change any assessment math
            when no pool allocates by ownership %.
          </p>
          {ownershipFormEditCount > 0 ? (
            <p className="mt-2 text-xs font-medium text-amber-900">
              ✓ Recorded. Re-promote (Approve) to apply it to the schedule.
            </p>
          ) : (
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={!reviewReady}
                onClick={() =>
                  onSaveEdit(
                    'unit_structure.ownership_percent_form',
                    ownershipForm,
                    'fraction',
                  )
                }
                className="rounded border border-amber-400 bg-white px-3 py-1 text-xs font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-50"
              >
                Decimals (0.0347 = 3.47%)
              </button>
              <button
                type="button"
                disabled={!reviewReady}
                onClick={() =>
                  onSaveEdit(
                    'unit_structure.ownership_percent_form',
                    ownershipForm,
                    'points',
                  )
                }
                className="rounded border border-amber-400 bg-white px-3 py-1 text-xs font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-50"
              >
                Points (3.47 = 3.47%)
              </button>
            </div>
          )}
        </section>
      )}

      {/* ── Units (with pagination) ───────────────────────────────── */}
      {units.length > 0 && (
        <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <div className="mb-3 flex items-end justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">
                Units <span className="font-normal text-slate-500">({units.length})</span>
              </h2>
              <p className="text-xs text-slate-500 tabular-nums">
                Showing {(unitsPage - 1) * UNITS_PAGE_SIZE + 1}–
                {Math.min(unitsPage * UNITS_PAGE_SIZE, units.length)} of{' '}
                {units.length}
              </p>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="py-2 pr-4 font-medium">#</th>
                  <th className="py-2 pr-4 text-right font-medium">Sq ft</th>
                  <th className="py-2 pr-4 text-right font-medium">Ownership %</th>
                  <th className="py-2 pr-4 font-medium">Category</th>
                  <th className="py-2 pr-4 font-medium">Flags</th>
                  <th className="py-2 pr-4 font-medium">Source</th>
                  <th className="py-2 pr-4 text-right font-medium">Confidence</th>
                  <th className="py-2 text-right font-medium">Factors</th>
                </tr>
              </thead>
              <tbody>
                {pagedUnits.map((u, i) => {
                  const unitId = String(u.unit_number || `${unitsPage}-${i}`);
                  const originalIndex = (unitsPage - 1) * UNITS_PAGE_SIZE + i;
                  const factors = Array.isArray(u.pool_factors)
                    ? (u.pool_factors as Array<Record<string, unknown>>)
                    : [];
                  const expanded = expandedUnitNumbers.has(unitId);
                  const resFlag = String(u.residential_commercial_flag || '');
                  const parkFlag = String(u.parking_flag || '');
                  return (
                    <Fragment key={`${unitsPage}-${i}`}>
                      <tr className="border-b border-slate-100 last:border-b-0">
                        <td className="py-2 pr-4 font-mono text-xs text-slate-700">
                          {String(u.unit_number || '')}
                        </td>
                        <td className="py-2 pr-4 text-right tabular-nums">
                          <EditableValue
                            value={u.square_feet ?? null}
                            fieldPath={`unit_structure.units[${originalIndex}].square_feet`}
                            onSave={onSaveEdit}
                            disabled={!reviewReady}
                            editCount={editCountByPath[`unit_structure.units[${originalIndex}].square_feet`]}
                            kind="number"
                            display={u.square_feet != null ? formatNumber(u.square_feet) : undefined}
                          />
                        </td>
                        <td className="py-2 pr-4 text-right tabular-nums">
                          <EditableValue
                            value={u.ownership_percent ?? null}
                            fieldPath={`unit_structure.units[${originalIndex}].ownership_percent`}
                            onSave={onSaveEdit}
                            disabled={!reviewReady}
                            editCount={editCountByPath[`unit_structure.units[${originalIndex}].ownership_percent`]}
                            kind="number"
                            display={
                              u.ownership_percent != null ? `${formatNumber(u.ownership_percent, 4)}%` : undefined
                            }
                          />
                        </td>
                        <td className="py-2 pr-4 text-slate-800">
                          <EditableValue
                            value={u.category || ''}
                            fieldPath={`unit_structure.units[${originalIndex}].category`}
                            onSave={onSaveEdit}
                            disabled={!reviewReady}
                            editCount={editCountByPath[`unit_structure.units[${originalIndex}].category`]}
                          />
                        </td>
                        <td className="py-2 pr-4">
                          <span className="inline-flex flex-wrap items-center gap-1">
                            {resFlag && (
                              <span
                                className={cn(
                                  'inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset',
                                  resFlag.toLowerCase().startsWith('commercial')
                                    ? 'bg-purple-50 text-purple-700 ring-purple-600/20'
                                    : 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
                                )}
                              >
                                {resFlag}
                              </span>
                            )}
                            <EditableValue
                              value={u.parking_flag || ''}
                              fieldPath={`unit_structure.units[${originalIndex}].parking_flag`}
                              onSave={onSaveEdit}
                              disabled={!reviewReady}
                              editCount={editCountByPath[`unit_structure.units[${originalIndex}].parking_flag`]}
                              display={
                                parkFlag && parkFlag.toLowerCase() !== 'no' ? (
                                  <span className="inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-inset ring-slate-300/60">
                                    parking: {parkFlag}
                                  </span>
                                ) : (
                                  <span className="text-slate-300">—</span>
                                )
                              }
                            />
                          </span>
                        </td>
                        <td className="py-2 pr-4">
                          <PageChips pages={u.source_page} />
                        </td>
                        <td className="py-2 pr-4 text-right">
                          <ManualOrConfidenceBadge isManual={isManual} value={u.confidence} />
                        </td>
                        <td className="py-2 text-right">
                          {factors.length === 0 ? (
                            <span className="text-slate-300">—</span>
                          ) : (
                            <button
                              type="button"
                              onClick={() => toggleUnit(unitId)}
                              aria-expanded={expanded}
                              className="inline-flex items-center gap-1 rounded border border-slate-200 px-2 py-0.5 text-xs text-slate-700 hover:bg-slate-50"
                            >
                              {factors.length} factor
                              {factors.length === 1 ? '' : 's'}
                              <span aria-hidden>{expanded ? '▴' : '▾'}</span>
                            </button>
                          )}
                        </td>
                      </tr>
                      {expanded && factors.length > 0 && (
                        <tr className="border-b border-slate-100 bg-slate-50/50">
                          <td colSpan={8} className="px-3 py-3">
                            <table className="min-w-full text-xs">
                              <thead>
                                <tr className="border-b border-slate-200 text-left uppercase tracking-wide text-slate-500">
                                  <th className="py-1 pr-3 font-medium">Pool</th>
                                  <th className="py-1 pr-3 font-medium">Label</th>
                                  <th className="py-1 pr-3 text-right font-medium">
                                    Value
                                  </th>
                                  <th className="py-1 pr-3 font-medium">Type</th>
                                  <th className="py-1 font-medium">Source</th>
                                </tr>
                              </thead>
                              <tbody>
                                {factors.map((f, fi) => {
                                  const ft = String(f.factor_type || 'raw_factor');
                                  const fv = f.factor_value;
                                  // Defensive parse: accept number, numeric
                                  // string ("0.0211"), or percent-suffixed
                                  // string ("2.11%"). Falls back to verbatim
                                  // when the value isn't parseable.
                                  const rawString = String(fv ?? '').replace('%', '').trim();
                                  const parsed = Number(rawString);
                                  let formattedValue: string = '—';
                                  if (fv == null || rawString === '') {
                                    formattedValue = '—';
                                  } else if (!Number.isFinite(parsed)) {
                                    formattedValue = String(fv);
                                  } else if (ft === 'percent') {
                                    // Percent factors store EITHER as decimal
                                    // fraction (sum ≈ 1.0) OR percentage points
                                    // (sum ≈ 100) depending on DRE convention.
                                    // Magnitude switch handles both without
                                    // anchoring on any specific value.
                                    const asPercent =
                                      Math.abs(parsed) <= 1 ? parsed * 100 : parsed;
                                    formattedValue = `${formatNumber(asPercent, 4)}%`;
                                  } else if (ft === 'dollar_amount') {
                                    formattedValue = formatCurrency(parsed);
                                  } else {
                                    formattedValue = formatNumber(parsed, 2);
                                  }
                                  return (
                                    <tr
                                      key={fi}
                                      className="border-b border-slate-200/60 last:border-b-0"
                                    >
                                      <td className="py-1 pr-3">
                                        <MonoPill>
                                          {String(f.pool_key || '')}
                                        </MonoPill>
                                      </td>
                                      <td
                                        className="max-w-[24ch] truncate py-1 pr-3 text-slate-700"
                                        title={String(f.factor_label || '')}
                                      >
                                        {String(f.factor_label || '')}
                                      </td>
                                      <td className="py-1 pr-3 text-right tabular-nums">
                                        {formattedValue}
                                      </td>
                                      <td className="py-1 pr-3">
                                        <MonoPill>{ft}</MonoPill>
                                      </td>
                                      <td className="py-1">
                                        <PageChips pages={f.source_page} />
                                      </td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Pagination
            page={unitsPage}
            pageSize={UNITS_PAGE_SIZE}
            total={units.length}
            onPageChange={setUnitsPage}
          />
        </section>
      )}

      {/* ── Reserve setup ────────────────────────────────────────── */}
      {reserveSetup && reserveRows.length > 0 && (
        <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-slate-900">Reserve setup</h2>
          <KeyValueGrid rows={reserveRows} />
        </section>
      )}

      {/* ── Recommended saved setup ──────────────────────────────── */}
      {recommendedSetup && recommendedRows.length > 0 && (
        <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-slate-900">
            Recommended setup
          </h2>
          <KeyValueGrid rows={recommendedRows} />
        </section>
      )}

      {/* ── Formulas ─────────────────────────────────────────────── */}
      {formulas.length > 0 && (
        <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-slate-900">
            Formulas{' '}
            <span className="font-normal text-slate-500">({formulas.length})</span>
          </h2>
          <ul className="space-y-2 text-sm">
            {formulas.map((f, i) => (
              <li key={i} className="leading-relaxed">
                <span className="font-medium text-slate-900">
                  {String(f.formula_name || '')}
                </span>
                <span className="text-slate-400">: </span>
                <span className="font-mono text-xs text-slate-700">
                  {String(f.formula_expression || '')}
                </span>
                {f.example_from_dre ? (
                  <span className="ml-2 text-xs text-slate-500">
                    (e.g. {String(f.example_from_dre)})
                  </span>
                ) : null}
                <span className="ml-2 inline-flex items-center gap-2 align-middle">
                  <PageChips pages={f.source_page} />
                  <ConfidenceBadge value={f.confidence} />
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ── Math validation checks ───────────────────────────────── */}
      {validationChecks.length > 0 && (
        <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-slate-900">
            Math validation checks
          </h2>
          <ul className="space-y-2 text-sm">
            {validationChecks.map((c, i) => {
              const pages = (c as Record<string, unknown>)['source_pages'];
              return (
                <li key={i} className="flex items-start gap-3">
                  <MathCheckStatus status={c.status} />
                  <div className="flex-1">
                    <span className="font-medium text-slate-900">{c.check_name}</span>
                    {c.details && (
                      <span className="ml-2 text-slate-600">{c.details}</span>
                    )}
                    {Array.isArray(pages) && pages.length > 0 && (
                      <span className="ml-2 inline-flex items-center align-middle">
                        <PageChips pages={pages} />
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {/* ── Edit history ─────────────────────────────────────────── */}
      <section className="rounded-md border border-slate-200 bg-slate-50/60 p-4">
        <h2 className="mb-2 text-sm font-semibold text-slate-900">
          Edit history{' '}
          <span className="font-normal text-slate-500">({edits.length})</span>
        </h2>
        {edits.length === 0 ? (
          <p className="text-sm text-slate-500">No edits yet.</p>
        ) : (
          <ul className="space-y-1 text-xs">
            {edits.map((e) => (
              <li key={e.edit_id} className="font-mono leading-relaxed">
                <span className="text-slate-500">
                  {e.edited_at.slice(0, 16)} {e.edited_by || '—'}
                </span>{' '}
                <span className="text-slate-800">{e.field_path}</span>
                <span className="text-slate-400">: </span>
                <span className="text-rose-700">{e.old_value || '∅'}</span>
                <span className="text-slate-400"> → </span>
                <span className="text-emerald-700">{e.new_value || '∅'}</span>
                {e.reason && (
                  <span className="ml-2 text-slate-500">({e.reason})</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* ── Page inventory (diagnostic, collapsed by default) ────── */}
      {pageInventory.length > 0 && (
        <section className="rounded-md border border-slate-200 bg-slate-50/60 p-4">
          <button
            type="button"
            onClick={() => setShowPageInventory((v) => !v)}
            aria-expanded={showPageInventory}
            className="flex w-full items-center justify-between text-left"
          >
            <span className="text-sm font-semibold text-slate-900">
              Page inventory{' '}
              <span className="font-normal text-slate-500">
                ({pageInventory.length} pages)
              </span>
            </span>
            <span className="text-xs text-slate-500" aria-hidden>
              {showPageInventory ? '▴ Hide' : '▾ Show'}
            </span>
          </button>
          {showPageInventory && (
            <div className="mt-3 overflow-x-auto">
              <table className="min-w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-200 text-left uppercase tracking-wide text-slate-500">
                    <th className="py-1 pr-3 font-medium">Page #</th>
                    <th className="py-1 pr-3 font-medium">Type</th>
                    <th className="py-1 pr-3 text-right font-medium">Confidence</th>
                    <th className="py-1 font-medium">Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {pageInventory.map((entry, i) => (
                    <tr
                      key={`${entry.page_number ?? i}`}
                      className="border-b border-slate-200/60 last:border-b-0"
                    >
                      <td className="py-1 pr-3 font-mono tabular-nums text-slate-700">
                        {entry.page_number != null ? (
                          <button
                            type="button"
                            onClick={() => jumpToPage(entry.page_number as number)}
                            title={`Jump to page ${entry.page_number} in the source PDF`}
                            className="rounded px-1 hover:bg-slate-200 hover:text-slate-900"
                          >
                            {entry.page_number}
                          </button>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="py-1 pr-3">
                        <MonoPill>{String(entry.page_type || '')}</MonoPill>
                      </td>
                      <td className="py-1 pr-3 text-right">
                        <ConfidenceBadge value={entry.confidence} />
                      </td>
                      <td
                        className="max-w-[60ch] truncate py-1 text-slate-700"
                        title={String(entry.notes || '')}
                      >
                        {String(entry.notes || '')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </section>
  );

  return (
    <PdfJumpContext.Provider value={jumpToPage}>
      {isCompareOpen ? (
        <DrePdfCompareView
          fileUrl={dreDocumentFileUrl(hoaId, detail.dre_document_id)}
          targetPage={targetPage}
          onClose={() => setIsCompareOpen(false)}
        >
          {mainContent}
        </DrePdfCompareView>
      ) : (
        mainContent
      )}
    </PdfJumpContext.Provider>
  );
}
