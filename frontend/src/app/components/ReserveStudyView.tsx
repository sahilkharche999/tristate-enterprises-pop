import { useRef, useState } from 'react';
import { AlertTriangle, ArrowDown, ArrowUp, Heading2, Minus, Plus, Save, Trash2, Upload } from 'lucide-react';

import { Button } from './ui/button';
import { Input } from './ui/input';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from './ui/alert-dialog';
import type { ReserveStudyRow } from '../api/budgetHistory';
import {
  getDisplayEstimatedLiability,
  getDisplayYearReplacementProvision,
} from '../lib/reserveStudyDerived';
import { clampTableZoomPercent, TABLE_ZOOM_STEP } from './tableZoom.ts';

interface ReserveStudyViewProps {
  rows: ReserveStudyRow[];
  warnings: string[];
  status: string;
  onRowChange: (index: number, field: keyof ReserveStudyRow, value: string) => void;
  onAddRow: () => void;
  onAddHeader: () => void;
  onMoveRow: (index: number, direction: 'up' | 'down') => void;
  onDeleteRow: (index: number) => void;
  onSave: () => Promise<void>;
  onApply: () => Promise<void>;
  onReplaceFile?: (file: File) => Promise<void>;
  hasUnsavedChanges: boolean;
  isSaving: boolean;
  isApplying: boolean;
  applyMessage: string | null;
  // Jumps the compare-view PDF pane to a row's cited page (add-reserve-study-pdf-compare-view).
  // A single flat table with one row-rendering loop — a plain callback prop is enough here,
  // unlike DRE's PdfJumpContext (needed there for ~10 nested PageChips call sites).
  onJumpToPage?: (page: number) => void;
  // Opens the full-screen compare view. Undefined (not just disabled) when there's no source
  // PDF to compare against (e.g. rows entered manually with no upload) — same conditional-prop
  // convention this file already uses for onReplaceFile.
  onOpenCompare?: () => void;
  // True when rendered inside the compare view's left pane rather than as the full page. That
  // pane can be a fraction of the viewport width, but Tailwind's `md:` breakpoints below key off
  // the browser viewport, not the pane — so the intro heading/paragraph (sized for a full-width
  // page) wrap onto many lines and push the table down. Compact mode drops that intro block and
  // lets the action row wrap normally instead of forcing a single line via `md:flex-nowrap`.
  compact?: boolean;
  // Pixel offset (measured by BudgetScreen from the page's own sticky header + tab bar) that
  // the table's sticky header row should sit below on the full page. Ignored when `compact` is
  // true — inside the compare view's left pane, the table's nearest scrolling ancestor is that
  // pane itself, so the header sticks to plain `top: 0` instead.
  stickyHeaderOffset?: number;
}

export function ReserveStudyView({
  rows,
  warnings,
  status,
  onRowChange,
  onAddRow,
  onAddHeader,
  onMoveRow,
  onDeleteRow,
  onSave,
  onApply,
  onReplaceFile,
  hasUnsavedChanges,
  isSaving,
  isApplying,
  applyMessage,
  onJumpToPage,
  onOpenCompare,
  compact = false,
  stickyHeaderOffset = 0,
}: ReserveStudyViewProps) {
  const [deleteIndex, setDeleteIndex] = useState<number | null>(null);
  const [isReplacing, setIsReplacing] = useState(false);
  const [tableZoomPercent, setTableZoomPercent] = useState(100);
  const replaceInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !onReplaceFile) return;
    setIsReplacing(true);
    try {
      await onReplaceFile(file);
    } finally {
      setIsReplacing(false);
      if (replaceInputRef.current) replaceInputRef.current.value = '';
    }
  };

  const pendingDeleteRow = deleteIndex === null ? null : rows[deleteIndex] ?? null;

  const confirmDeleteRow = () => {
    if (deleteIndex === null) return;
    onDeleteRow(deleteIndex);
    setDeleteIndex(null);
  };

  return (
    <section className={compact ? '' : 'space-y-6'}>
      <div className={compact ? 'p-4' : 'rounded-2xl border border-[#e5e5e5] bg-white p-6 shadow-sm'}>
        <div className={compact ? 'flex flex-col gap-3' : 'flex flex-col gap-4 md:flex-row md:items-start md:justify-between'}>
          {compact ? null : (
            <div className="space-y-2">
              <p className="text-xs font-medium uppercase tracking-[0.2em] text-[#737373]">
                Reserve Study Review
              </p>
              <h2 className="text-xl font-semibold text-[#111111]">Editable Reserve Components</h2>
              <p className="max-w-2xl text-sm text-[#666666]">
                Review the parsed reserve-study rows here, fix blanks manually, then apply only the
                due-this-year items into the budget draft.
              </p>
            </div>
          )}
          <div className={compact ? 'flex flex-wrap items-center gap-2' : 'flex flex-wrap items-center gap-2 md:flex-nowrap md:justify-end'}>
            <span className="whitespace-nowrap rounded-full bg-[#f5f5f5] px-3 py-1 text-xs font-medium text-[#525252]">
              {status || 'none'}
            </span>
            {hasUnsavedChanges ? (
              <span className="whitespace-nowrap rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-900">
                Unsaved changes
              </span>
            ) : null}
            {onOpenCompare ? (
              <Button
                type="button"
                variant="outline"
                onClick={onOpenCompare}
                className="whitespace-nowrap border-[#d4d4d4] text-[#525252] hover:bg-[#f5f5f5]"
              >
                Compare with PDF
              </Button>
            ) : null}
            {onReplaceFile ? (
              <>
                <input
                  ref={replaceInputRef}
                  type="file"
                  accept=".pdf,application/pdf"
                  className="hidden"
                  onChange={(e) => void handleFileChange(e)}
                />
                <Button
                  type="button"
                  variant="outline"
                  disabled={isReplacing}
                  onClick={() => replaceInputRef.current?.click()}
                  className="whitespace-nowrap border-[#d4d4d4] text-[#525252] hover:bg-[#f5f5f5] gap-1.5"
                >
                  <Upload className="h-4 w-4" />
                  {isReplacing ? 'Replacing…' : 'Replace PDF'}
                </Button>
              </>
            ) : null}
            <Button
              variant="outline"
              onClick={() => void onSave()}
              disabled={isSaving || !hasUnsavedChanges}
              className="whitespace-nowrap border-[#d4d4d4] text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
            >
              <Save className="mr-2 h-4 w-4" />
              {isSaving ? 'Saving...' : 'Save Reserve Study'}
            </Button>
            <Button
              onClick={() => void onApply()}
              disabled={isSaving || isApplying || rows.length === 0}
              className="whitespace-nowrap bg-[#111111] text-white shadow-sm hover:bg-[#262626]"
            >
              {isApplying ? 'Applying...' : 'Apply to Budget'}
            </Button>
          </div>
        </div>

        {warnings.length > 0 ? (
          <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium text-amber-900">
              <AlertTriangle className="h-4 w-4" />
              Review warnings
            </div>
            <ul className="space-y-1 text-sm text-amber-900">
              {warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {applyMessage ? (
          <div className="mt-5 rounded-xl border border-[#d4d4d4] bg-[#fafafa] px-4 py-3 text-sm text-[#525252]">
            {applyMessage}
          </div>
        ) : null}

        <div className={`${compact ? 'mt-3' : 'mt-6'} flex items-center justify-end gap-1.5`}>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => setTableZoomPercent((z) => clampTableZoomPercent(z - TABLE_ZOOM_STEP))}
            disabled={tableZoomPercent <= 50}
            className="h-7 w-7 text-[#525252] hover:bg-[#f5f5f5]"
            aria-label="Zoom out"
          >
            <Minus className="h-3.5 w-3.5" />
          </Button>
          <button
            type="button"
            onClick={() => setTableZoomPercent(100)}
            title="Reset zoom"
            className="w-12 text-center text-xs font-medium text-[#737373] hover:text-[#111111]"
          >
            {tableZoomPercent}%
          </button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => setTableZoomPercent((z) => clampTableZoomPercent(z + TABLE_ZOOM_STEP))}
            disabled={tableZoomPercent >= 150}
            className="h-7 w-7 text-[#525252] hover:bg-[#f5f5f5]"
            aria-label="Zoom in"
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
        {/* Full page: the table gets its own bounded scroll box (max-h-70vh + overflow-auto) so
            the <thead> sticks to the box top as rows scroll — the standard wide-data-grid pattern.
            In compact mode (the compare view's left pane) the pane itself already scrolls both
            axes, so we drop the cap and the inner overflow/clip — otherwise the 70vh box leaves a
            large empty gap below it inside the full-height pane. */}
        <div className={`mt-2 rounded-2xl border border-[#e5e5e5] ${compact ? '' : 'overflow-hidden'}`}>
          <div
            className={compact ? undefined : 'max-h-[70vh] overflow-auto'}
            style={{ zoom: tableZoomPercent / 100 }}
          >
            <table className="min-w-[1540px] bg-white">
              <colgroup>
                <col className="w-[72px]" />
                <col className="w-[300px]" />
                <col className="w-[110px]" />
                <col className="w-[128px]" />
                <col className="w-[176px]" />
                <col className="w-[150px]" />
                <col className="w-[126px]" />
                <col className="w-[126px]" />
                <col className="w-[200px]" />
                <col className="w-[96px]" />
              </colgroup>
              <thead
                className="sticky top-0 z-10 bg-[#f7f7f7] shadow-sm"
              >
                <tr className="text-left text-xs uppercase tracking-[0.18em] text-[#737373]">
                  <th className="px-4 py-3">Move</th>
                  <th className="px-4 py-3">Line Item</th>
                  <th className="px-4 py-3">Useful Life</th>
                  <th className="px-4 py-3">Remaining Life</th>
                  <th className="px-4 py-3">Quantity / Info</th>
                  <th className="px-4 py-3">Replacement Cost</th>
                  <th className="px-4 py-3">Year Rplc. Prov.</th>
                  <th className="px-4 py-3">Est. Liab.</th>
                  <th className="px-4 py-3">Flags</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={10} className="px-4 py-8 text-center text-sm text-[#737373]">
                      No reserve-study rows yet. Add one manually or upload a reserve-study PDF.
                    </td>
                  </tr>
                ) : (
                  rows.map((row, index) => row.row_type === 'header' ? (
                    <tr key={row.row_id || `reserve-row-${index}`} className="border-t border-[#e5e5e5] bg-[#fafafa]">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => onMoveRow(index, 'up')}
                            disabled={index === 0}
                            className="h-8 w-8 p-0 text-[#525252] hover:bg-white"
                            aria-label="Move header up"
                          >
                            <ArrowUp className="h-4 w-4" />
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => onMoveRow(index, 'down')}
                            disabled={index === rows.length - 1}
                            className="h-8 w-8 p-0 text-[#525252] hover:bg-white"
                            aria-label="Move header down"
                          >
                            <ArrowDown className="h-4 w-4" />
                          </Button>
                        </div>
                      </td>
                      <td className="px-4 py-3" colSpan={7}>
                        <div className="flex items-center gap-3">
                          <Heading2 className="h-4 w-4 text-[#737373]" />
                          <Input
                            value={row.line_item}
                            onChange={(event) => onRowChange(index, 'line_item', event.target.value)}
                            className="min-w-[360px] border-[#d4d4d4] bg-white font-semibold text-[#111111]"
                          />
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="text-xs font-medium uppercase tracking-[0.18em] text-[#737373]">
                          Header
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDeleteIndex(index)}
                          className="text-red-600 hover:bg-red-50 hover:text-red-700"
                        >
                          <Trash2 className="mr-2 h-4 w-4" />
                          Delete
                        </Button>
                      </td>
                    </tr>
                  ) : (
                    <tr key={row.row_id || `reserve-row-${index}`} className="border-t border-[#f0f0f0]">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => onMoveRow(index, 'up')}
                            disabled={index === 0}
                            className="h-8 w-8 p-0 text-[#525252] hover:bg-[#f5f5f5]"
                            aria-label="Move row up"
                          >
                            <ArrowUp className="h-4 w-4" />
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => onMoveRow(index, 'down')}
                            disabled={index === rows.length - 1}
                            className="h-8 w-8 p-0 text-[#525252] hover:bg-[#f5f5f5]"
                            aria-label="Move row down"
                          >
                            <ArrowDown className="h-4 w-4" />
                          </Button>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <Input
                          value={row.line_item}
                          onChange={(event) => onRowChange(index, 'line_item', event.target.value)}
                          className="min-w-[220px] border-[#e5e5e5] bg-white"
                        />
                        {row.source_page != null ? (
                          onJumpToPage ? (
                            <button
                              type="button"
                              onClick={() => onJumpToPage(row.source_page as number)}
                              title={`Jump to page ${row.source_page} in the source PDF`}
                              className="mt-1 inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-inset ring-slate-300/60 hover:bg-slate-200 hover:ring-slate-400"
                            >
                              Page {row.source_page}
                            </button>
                          ) : (
                            <span className="mt-1 inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-inset ring-slate-300/60">
                              Page {row.source_page}
                            </span>
                          )
                        ) : null}
                      </td>
                      <td className="px-4 py-3">
                        <Input
                          type="number"
                          value={row.useful_life ?? ''}
                          onChange={(event) => onRowChange(index, 'useful_life', event.target.value)}
                          className="w-24 border-[#e5e5e5] bg-white"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <Input
                          type="number"
                          value={row.remaining_life ?? ''}
                          onChange={(event) => onRowChange(index, 'remaining_life', event.target.value)}
                          className="w-28 border-[#e5e5e5] bg-white"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <Input
                          value={row.quantity ?? ''}
                          onChange={(event) => onRowChange(index, 'quantity', event.target.value)}
                          className="w-40 border-[#e5e5e5] bg-white"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <Input
                          type="number"
                          value={row.replacement_cost ?? ''}
                          onChange={(event) => onRowChange(index, 'replacement_cost', event.target.value)}
                          className="w-36 border-[#e5e5e5] bg-white"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <div className="min-w-[108px] rounded-md border border-[#e5e5e5] bg-[#fafafa] px-3 py-2 text-sm text-[#404040]">
                          {getDisplayYearReplacementProvision(row) ?? '—'}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="min-w-[108px] rounded-md border border-[#e5e5e5] bg-[#fafafa] px-3 py-2 text-sm text-[#404040]">
                          {getDisplayEstimatedLiability(row) ?? '—'}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="min-w-[168px]">
                          <div className="flex flex-wrap gap-2">
                          {(row.flags ?? []).length > 0 ? (
                            row.flags?.map((flag) => (
                              <span
                                key={`${row.row_id}-${flag}`}
                                className="rounded-full bg-amber-100 px-2.5 py-1 text-[11px] font-medium text-amber-900"
                              >
                                {flag}
                              </span>
                            ))
                          ) : (
                            <span className="text-xs text-[#a3a3a3]">No flags</span>
                          )}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDeleteIndex(index)}
                          className="text-red-600 hover:bg-red-50 hover:text-red-700"
                        >
                          <Trash2 className="mr-2 h-4 w-4" />
                          Delete
                        </Button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="mt-4">
          <div className="flex flex-wrap gap-3">
            <Button
              variant="outline"
              onClick={onAddRow}
              className="border-dashed border-[#d4d4d4] text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
            >
              <Plus className="mr-2 h-4 w-4" />
              Add Row
            </Button>
            <Button
              variant="outline"
              onClick={onAddHeader}
              className="border-dashed border-[#d4d4d4] text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
            >
              <Heading2 className="mr-2 h-4 w-4" />
              Add Header
            </Button>
          </div>
        </div>
      </div>
      <AlertDialog
        open={deleteIndex !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteIndex(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Are you sure?</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDeleteRow
                ? `This will delete "${pendingDeleteRow.line_item}" from the reserve study table.`
                : 'This will delete the selected reserve study row.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDeleteRow}
              className="bg-red-600 text-white hover:bg-red-700"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
