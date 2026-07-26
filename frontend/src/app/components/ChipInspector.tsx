/**
 * The popover behind a chip click in the narrative editor.
 *
 * A chip renders as its *name* ("CPA firm name"), which leaves two questions
 * unanswered: what will actually print there, and where do I change it. Both
 * are otherwise a PDF generation away. This answers them in place.
 *
 * The honesty rule this component exists to keep: a missing entry in `values`
 * means "not knowable right now", never zero. The backend deliberately
 * withholds computed chips it cannot compute, because `build_var_map` renders
 * unknown money as `$0.00` and an operator would read that as a real figure.
 * So `value === undefined` must never be rendered as a blank or a dash that
 * could pass for content — it gets its own explanation.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ExternalLink, X } from 'lucide-react';
import type { BoilerplateVariable, ChipSourceKind } from '../api/hoaSettings';

const POPOVER_WIDTH = 320;
const GAP = 8;

export interface ChipInspectorTarget {
  chip: BoilerplateVariable;
  kind: 'value' | 'block';
  rect: DOMRect;
}

const SOURCE_LABEL: Record<ChipSourceKind, string> = {
  settings: 'Disclosure settings',
  property: 'HOA record',
  computed: 'Computed',
  derived: 'Automatic',
};

const SOURCE_STYLE: Record<ChipSourceKind, string> = {
  settings: 'bg-blue-50 text-blue-700 border-blue-200',
  property: 'bg-violet-50 text-violet-700 border-violet-200',
  computed: 'bg-amber-50 text-amber-800 border-amber-200',
  derived: 'bg-[#f5f5f5] text-[#525252] border-[#e5e5e5]',
};

function ValueRow({
  kind,
  chip,
  value,
  valuesLoaded,
  unavailableReason,
}: {
  kind: 'value' | 'block';
  chip: BoilerplateVariable;
  value: string | undefined;
  valuesLoaded: boolean;
  unavailableReason: string | null;
}) {
  // Block chips are whole paragraphs and tables built at generate time; there
  // is no single value to preview, and pretending otherwise would mislead.
  if (kind === 'block') {
    return (
      <p className="text-sm text-[#525252]">
        Written into the package when it is generated.
      </p>
    );
  }

  if (value === undefined) {
    if (!valuesLoaded) {
      return <p className="text-sm text-[#a3a3a3]">Checking…</p>;
    }
    return (
      <div className="space-y-1">
        <p className="text-sm text-[#525252]">
          {chip.source === 'computed'
            ? 'Not calculated yet — this fills in when the package is generated.'
            : 'Not available for this HOA yet.'}
        </p>
        {unavailableReason ? (
          <p className="text-xs text-[#a3a3a3]">{unavailableReason}</p>
        ) : null}
      </div>
    );
  }

  // A genuinely empty value is a real, intended outcome for the optional-clause
  // chips — they disappear rather than print. Say so, rather than showing a
  // blank box the operator will read as a bug.
  if (value === '') {
    return (
      <p className="text-sm italic text-[#737373]">
        Blank — nothing prints here.
      </p>
    );
  }

  return (
    <p className="break-words text-sm font-medium text-[#111111]">{value}</p>
  );
}

export function ChipInspector({
  target,
  value,
  valuesLoaded,
  unavailableReason,
  onEdit,
  onClose,
}: {
  target: ChipInspectorTarget;
  value: string | undefined;
  valuesLoaded: boolean;
  unavailableReason: string | null;
  onEdit: (tab: 'disclosure' | 'database', field: string) => void;
  onClose: () => void;
}) {
  const { chip, kind, rect } = target;
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ top: rect.bottom + GAP, left: rect.left });

  // Keep the popover on screen: flip above the chip when it would overflow the
  // bottom, and clamp horizontally.
  useLayoutEffect(() => {
    const height = ref.current?.offsetHeight ?? 160;
    const flip = rect.bottom + GAP + height > window.innerHeight;
    setPos({
      top: flip ? Math.max(GAP, rect.top - GAP - height) : rect.bottom + GAP,
      left: Math.min(
        Math.max(GAP, rect.left),
        Math.max(GAP, window.innerWidth - POPOVER_WIDTH - GAP),
      ),
    });
  }, [rect]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    const onPointer = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    // `capture` on keydown so Esc closes the popover before the workbench's
    // own Esc handler tries to close the whole editor.
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('mousedown', onPointer);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('mousedown', onPointer);
    };
  }, [onClose]);

  const canEdit = chip.settings_field !== null && chip.settings_tab !== null;
  const isOverride = chip.source === 'computed' && canEdit;

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={`${chip.label} details`}
      style={{ top: pos.top, left: pos.left, width: POPOVER_WIDTH }}
      className="fixed z-[60] rounded-lg border border-[#e5e5e5] bg-white p-3 shadow-lg"
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-semibold text-[#111111]">{chip.label}</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="-mr-1 -mt-1 rounded p-1 text-[#a3a3a3] hover:bg-[#f5f5f5] hover:text-[#525252]"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-2 rounded border border-[#eeeeee] bg-[#fafafa] px-2.5 py-2">
        <ValueRow
          kind={kind}
          chip={chip}
          value={value}
          valuesLoaded={valuesLoaded}
          unavailableReason={unavailableReason}
        />
      </div>

      <div className="mt-2.5 flex items-start gap-2">
        <span
          className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${SOURCE_STYLE[chip.source]}`}
        >
          {SOURCE_LABEL[chip.source]}
        </span>
      </div>
      <p className="mt-1.5 text-xs leading-5 text-[#737373]">{chip.source_note}</p>

      {canEdit ? (
        <button
          type="button"
          onClick={() => onEdit(chip.settings_tab!, chip.settings_field!)}
          className="mt-2.5 inline-flex items-center gap-1.5 text-xs font-medium text-[#111111] underline underline-offset-2 hover:text-[#404040]"
        >
          {isOverride ? 'Override in settings' : 'Edit in settings'}
          <ExternalLink className="h-3 w-3" />
        </button>
      ) : null}
    </div>
  );
}
