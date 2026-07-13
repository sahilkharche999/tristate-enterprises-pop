import { useState } from 'react';
import { GitMerge, Lock, MessageSquare, ChevronDown, Percent, DollarSign, Unlock, Minus, Plus, Upload } from 'lucide-react';
import { toast } from 'sonner';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { saveBudgetNote } from '../api/budgetHistory';
import { type LineItem } from '../data/mockData';
import { mergedBadgeLabel, mergedBadgeTooltip } from '../lib/glMerge.ts';
import { clampTableZoomPercent, TABLE_ZOOM_STEP } from './tableZoom.ts';
import { parseSourcePage } from '../lib/incomeStatementSourcePage.ts';
import {
  formatCurrency,
  getCategoryLabel,
  calcProposed,
  calcMonthly,
  calcPercentDiff,
  calcDisplayCategoryTotal,
  calcDisplayMonthly,
  calcDisplayProposed,
  calcSettingsDerivedReservePercent,
  calcTotalIncome,
  isReserveComponent,
} from '../lib/budget';
import { getErrorMessage } from '../lib/errors';

interface EnrichedViewProps {
  hoaId: string;
  draftId: number | null;
  lineItems: LineItem[];
  onPercentChange: (itemId: string, newPercent: number) => void;
  onFieldChange: (itemId: string, field: 'name' | 'ytdActual' | 'annualBudget' | 'projection', value: string) => void;
  onNoteSaved: (itemId: string, title: string, body: string) => void;
  onRequestMerge: (itemId: string) => void;
  onReadOnlyOverride?: (itemId: string, override: boolean | null) => void;
  units: number;
  reserveInflationRate: number;
  hasUnsavedChanges?: boolean;
  // Jumps the compare-view's source pane to a row's cited page (PDF-sourced drafts only —
  // add-income-statement-pdf-compare-view). A plain callback prop, mirroring ReserveStudyView's
  // single flat table pattern.
  onJumpToPage?: (page: number) => void;
  // Opens the full-screen compare view. Undefined when there's no source upload to compare
  // against at all; whether it points at a PDF or an Excel-rendered-as-HTML endpoint is decided
  // by the caller, not this component.
  onOpenCompare?: () => void;
  // Opens the OS file picker to swap the starting budget file on this draft
  // (rebuilds line items from the new file; keeps the reserve study + versions).
  // Undefined when replacement isn't applicable (e.g. inside the compare pane).
  onReplaceBudgetFile?: () => void;
  isReplacingBudgetFile?: boolean;
  // True when rendered inside the compare view's left pane rather than as the full page —
  // mirrors ReserveStudyView's compact prop (tightens spacing; this view has no intro-text
  // block to suppress).
  compact?: boolean;
  // Pixel offset (measured by BudgetScreen from the page's own sticky header + tab bar) that
  // the table's sticky header row should sit below on the full page. Ignored when `compact` is
  // true — inside the compare view's left pane, the table's nearest scrolling ancestor is that
  // pane itself (no page chrome above it there), so the header sticks to plain `top: 0` instead.
  stickyHeaderOffset?: number;
}

export function EnrichedView({
  hoaId,
  draftId,
  lineItems,
  onPercentChange,
  onFieldChange,
  onNoteSaved,
  onRequestMerge,
  onReadOnlyOverride,
  units,
  reserveInflationRate,
  hasUnsavedChanges = false,
  onJumpToPage,
  onOpenCompare,
  onReplaceBudgetFile,
  isReplacingBudgetFile = false,
  compact = false,
  stickyHeaderOffset = 0,
}: EnrichedViewProps) {
  const [expandedNote, setExpandedNote] = useState<string | null>(null);
  const [noteEdits, setNoteEdits] = useState<Record<string, { title: string; body: string }>>({});
  const [inputMode, setInputMode] = useState<Record<string, 'percent' | 'dollar'>>({});
  const [savingNoteId, setSavingNoteId] = useState<string | null>(null);
  const [tableZoomPercent, setTableZoomPercent] = useState(100);


  const handlePercentChangeInput = (itemId: string, value: string) => {
    const numValue = parseFloat(value) || 0;
    onPercentChange(itemId, numValue);
  };

  const handleDollarChangeInput = (itemId: string, dollarValue: string, annualBudget: number) => {
    const numValue = parseFloat(dollarValue) || 0;
    // Invert backend formula: proposed = annualBudget × (1 + % change)
    const percentChange = annualBudget > 0 ? ((numValue / annualBudget) - 1) * 100 : 0;
    onPercentChange(itemId, percentChange);
  };

  const toggleInputMode = (itemId: string) => {
    setInputMode(prev => ({
      ...prev,
      [itemId]: prev[itemId] === 'dollar' ? 'percent' : 'dollar'
    }));
  };

  const toggleNote = (itemId: string) => {
    if (expandedNote === itemId) {
      setExpandedNote(null);
    } else {
      setExpandedNote(itemId);
      const item = lineItems.find((i) => i.id === itemId);
      if (item?.note && !noteEdits[itemId]) {
        setNoteEdits((prev) => ({
          ...prev,
          [itemId]: item.note!,
        }));
      }
    }
  };

  const saveNote = async (itemId: string) => {
    if (!draftId) {
      toast.error('Upload an income statement before saving notes.');
      return;
    }

    const edit = noteEdits[itemId];
    const item = lineItems.find((entry) => entry.id === itemId);
    if (edit && item) {
      setSavingNoteId(itemId);
      try {
        await saveBudgetNote(hoaId, {
          draft_id: draftId,
          note_scope: 'line_item',
          line_item_key: String(item.accountCode ?? item.label ?? item.name),
          title: edit.title,
          body: edit.body,
        });
        onNoteSaved(itemId, edit.title, edit.body);
        toast.success('Note saved to sync history.');
        setExpandedNote(null);
      } catch (error) {
        toast.error(getErrorMessage(error, 'Failed to save note.'));
      } finally {
        setSavingNoteId(null);
      }
    }
  };

  const groupedItems = lineItems.reduce((acc, item) => {
    if (!acc[item.category]) {
      acc[item.category] = [];
    }
    acc[item.category].push(item);
    return acc;
  }, {} as Record<string, LineItem[]>);

  // Live mirror: sum of reserve allocation/transfer lines for Reserve Income display.
  // Backend computes the authoritative value on save; this keeps the UI in sync while editing.
  const liveTransferTotal = lineItems.reduce((sum, item) => {
    if (item.category === 'reserve_expense' && item.reserveGroup === 'transfer') {
      return sum + (item.annualBudget || 0);
    }
    return sum;
  }, 0);

  // Total Annual Budget = expenses only (operating + reserve expenses), not reserve income.
  // Assessment math depends on this definition — do not change.
  const totalAnnualBudget =
    calcDisplayCategoryTotal(groupedItems['operating'] || [], 'proposedChange', reserveInflationRate) +
    calcDisplayCategoryTotal(groupedItems['reserve'] || [], 'proposedChange', reserveInflationRate) +
    calcDisplayCategoryTotal(groupedItems['reserve_expense'] || [], 'proposedChange', reserveInflationRate);

  const totalIncome = calcTotalIncome(groupedItems['income'] || [], reserveInflationRate);
  const netSurplusDeficit = totalIncome - totalAnnualBudget;

  const perUnitMonthly = units > 0 ? totalAnnualBudget / 12 / units : null;

  return (
    <div className={compact ? 'space-y-2' : 'space-y-3'}>
      {/* Single action row: compare/unsaved on the left, zoom on the right — kept on one line
          so there's no stacked vertical gap between the controls and the table below. */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
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
              Compare with source
            </Button>
          ) : null}
          {!compact && onReplaceBudgetFile ? (
            <Button
              type="button"
              variant="outline"
              onClick={onReplaceBudgetFile}
              disabled={isReplacingBudgetFile}
              className="whitespace-nowrap gap-1.5 border-[#d4d4d4] text-[#525252] hover:bg-[#f5f5f5]"
              title="Swap the starting budget file for this draft. Line items are rebuilt from the new file; the attached reserve study and any generated versions are kept — no need to delete the disclosure package."
            >
              <Upload className="h-3.5 w-3.5" />
              {isReplacingBudgetFile ? 'Replacing...' : 'Replace Budget File'}
            </Button>
          ) : null}
        </div>
        <div className="flex items-center gap-1.5">
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
      </div>
      {/* Table. No `overflow-hidden`/`overflow-x-auto` on the wrappers: per the CSS spec an
          `overflow` value other than `visible` makes that element the sticky containing block,
          which would scope the sticky <thead> to the table box (sticking it partway *into* the
          table) instead of to the page. The table is `w-full` so it fits without horizontal
          scroll; the header then sticks relative to the page, flush under the tab bar. */}
      <div className="bg-white border border-[#e5e5e5] rounded-lg shadow-sm">
        <div style={{ zoom: tableZoomPercent / 100 }}>
          <table className="w-full">
            <thead
              className="sticky z-10 bg-[#fafafa] border-b border-[#e5e5e5] shadow-sm"
              // The thead lives inside the `zoom`ed div, which scales its sticky `top` too. Divide
              // the (unzoomed, viewport-measured) offset by the zoom factor so it still lands flush
              // under the tab bar at any zoom level (at 90% a raw 222px would render at ~200px and
              // tuck under the bar).
              style={{ top: compact ? 0 : stickyHeaderOffset / (tableZoomPercent / 100) }}
            >
              <tr>
                <th className="text-left px-6 py-4 text-xs font-semibold text-[#525252] uppercase tracking-wider min-w-[260px] w-[260px]">Line Item</th>
                <th className="text-right px-6 py-4 text-xs font-semibold text-[#525252] uppercase tracking-wider">YTD Actual</th>
                <th className="text-right px-6 py-4 text-xs font-semibold text-[#525252] uppercase tracking-wider">Annual Budget</th>
                <th className="text-right px-6 py-4 text-xs font-semibold text-[#525252] uppercase tracking-wider">% Difference</th>
                <th className="text-right px-6 py-4 text-xs font-semibold text-[#525252] uppercase tracking-wider">Projection</th>
                <th className="text-right px-6 py-4 text-xs font-semibold text-[#525252] uppercase tracking-wider">% Change</th>
                <th className="text-right px-6 py-4 text-xs font-semibold text-[#525252] uppercase tracking-wider">Proposed Change</th>
                <th className="text-right px-6 py-4 text-xs font-semibold text-[#525252] uppercase tracking-wider">Monthly</th>
                <th className="text-center px-6 py-4 text-xs font-semibold text-[#525252] uppercase tracking-wider">Notes</th>
                <th className="text-right px-6 py-4 text-xs font-semibold text-[#525252] uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(groupedItems).flatMap(([category, items]) => [
                // Category Header
                <tr key={`${category}-header`} className="bg-[#f5f5f5] border-b border-[#e5e5e5]">
                  <td colSpan={10} className="px-6 py-3.5 text-xs font-bold text-[#111111] uppercase tracking-wide">
                    {getCategoryLabel(category)}
                  </td>
                </tr>,
                // Line Items
                ...items.flatMap((item) => {
                  const isNoteExpanded = expandedNote === item.id;
                  const mergedLabel = mergedBadgeLabel(item);
                  const mergedTooltip = mergedBadgeTooltip(item) ?? undefined;

                  // ── Read-only row (reserve-study / reserve-labeled, excluded from budget flow) ──
                  if (item.readOnly) {
                    const isReserveIncomeLine =
                      item.category === 'reserve_income' && item.reserveGroup !== 'income';
                    // Live-mirror: show the transfer total for reserve income contribution lines.
                    const displayAnnualBudget = isReserveIncomeLine
                      ? liveTransferTotal
                      : item.annualBudget;
                    const canUnlock = !!onReadOnlyOverride;

                    if (isReserveComponent(item)) {
                      const adjustedReserveAmount = calcDisplayProposed(item, reserveInflationRate);
                      const adjustedMonthly = calcDisplayMonthly(item, reserveInflationRate);
                      const settingsPercent = calcSettingsDerivedReservePercent(item, reserveInflationRate);
                      return [
                        <tr key={item.id} className="border-b border-[#e5e5e5] bg-[#fcfcfc]">
                          <td className="px-6 py-4 text-sm text-[#525252] italic">
                            <div className="flex flex-col gap-1">
                              <span>{item.name}</span>
                              {mergedLabel ? (
                                <span
                                  title={mergedTooltip}
                                  className="inline-flex w-fit rounded-full border border-[#d4d4d4] bg-[#fafafa] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[#525252]"
                                >
                                  {mergedLabel}
                                </span>
                              ) : null}
                            </div>
                          </td>
                          <td className="px-6 py-4 text-sm text-[#737373] text-right font-mono">{formatCurrency(item.ytdActual)}</td>
                          <td className="px-6 py-4 text-sm text-[#737373] text-right font-mono">{formatCurrency(item.annualBudget)}</td>
                          <td className="px-6 py-4 text-sm text-[#a3a3a3] text-right">—</td>
                          <td className="px-6 py-4 text-sm text-[#a3a3a3] text-right">—</td>
                          <td className="px-6 py-4 text-right text-xs font-medium text-[#525252]">
                            Settings {settingsPercent.toFixed(1)}%
                          </td>
                          <td className="px-6 py-4 text-sm font-semibold text-[#111111] text-right font-mono">
                            {formatCurrency(adjustedReserveAmount)}
                          </td>
                          <td className="px-6 py-4 text-sm text-[#737373] text-right font-mono">
                            {formatCurrency(adjustedMonthly)}
                          </td>
                          <td className="px-6 py-4 text-center">
                            <button disabled className="p-2 rounded text-[#d4d4d4] cursor-not-allowed">
                              <MessageSquare className="w-4 h-4" />
                            </button>
                          </td>
                          <td className="px-6 py-4 text-right">
                            {canUnlock ? (
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => onReadOnlyOverride(item.id, false)}
                                className="border-[#d4d4d4] text-[#525252] hover:border-[#a3a3a3] hover:bg-[#f5f5f5] gap-1"
                                title="Unlock to edit this reserve line"
                              >
                                <Unlock className="w-3 h-3" />
                                Unlock
                              </Button>
                            ) : (
                              <Button
                                type="button"
                                variant="outline"
                                size="icon"
                                disabled
                                className="h-8 w-8 border-[#e5e5e5] text-[#d4d4d4]"
                                title="Cannot merge read-only reserve rows"
                              >
                                <GitMerge className="w-3.5 h-3.5" />
                              </Button>
                            )}
                          </td>
                        </tr>,
                      ];
                    }
                    return [
                      <tr key={item.id} className={`border-b border-[#e5e5e5] ${isReserveIncomeLine ? '' : 'opacity-60'}`}>
                        <td className="px-6 py-4 text-sm text-[#525252] italic">
                          <div className="flex flex-col gap-1">
                            <span>{item.name}</span>
                            {isReserveIncomeLine ? (
                              <span className="text-[10px] text-[#737373]">Mirrors allocation — derived from transfer lines</span>
                            ) : null}
                            {mergedLabel ? (
                              <span
                                title={mergedTooltip}
                                className="inline-flex w-fit rounded-full border border-[#d4d4d4] bg-[#fafafa] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[#525252]"
                              >
                                {mergedLabel}
                              </span>
                            ) : null}
                          </div>
                        </td>
                        <td className="px-6 py-4 text-sm text-[#737373] text-right font-mono">{formatCurrency(item.ytdActual)}</td>
                        <td className="px-6 py-4 text-sm text-[#737373] text-right font-mono">{formatCurrency(displayAnnualBudget)}</td>
                        <td className="px-6 py-4 text-sm text-[#a3a3a3] text-right">—</td>
                        <td className="px-6 py-4 text-sm text-[#a3a3a3] text-right">—</td>
                        <td className="px-6 py-4 text-sm text-[#a3a3a3] text-right text-xs">{isReserveIncomeLine ? 'Derived' : 'Excluded'}</td>
                        <td className="px-6 py-4 text-sm text-[#a3a3a3] text-right">—</td>
                        <td className="px-6 py-4 text-sm text-[#a3a3a3] text-right">—</td>
                        <td className="px-6 py-4 text-center">
                          <button disabled className="p-2 rounded text-[#d4d4d4] cursor-not-allowed">
                            <MessageSquare className="w-4 h-4" />
                          </button>
                        </td>
                        <td className="px-6 py-4 text-right">
                          {canUnlock && !isReserveIncomeLine ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => onReadOnlyOverride(item.id, false)}
                              className="border-[#d4d4d4] text-[#525252] hover:border-[#a3a3a3] hover:bg-[#f5f5f5] gap-1"
                              title="Unlock to edit this reserve line. It stays in the reserve bucket — edits change its value, not the operating total."
                            >
                              <Unlock className="w-3 h-3" />
                              Unlock
                            </Button>
                          ) : (
                            <span />
                          )}
                        </td>
                      </tr>,
                    ];
                  }

                  // An explicitly-unlocked reserve line: show inline hint + lock button.
                  const wasUnlocked = item.readOnlyOverride === false;

                  const projection = item.projection ?? 0;
                  const proposedChange = calcProposed(item.annualBudget, item.percentChange);
                  const monthly = calcMonthly(proposedChange);
                  const percentDiff = calcPercentDiff(projection, item.annualBudget);

                  return [
                    <tr key={item.id} className={`border-b border-[#e5e5e5] hover:bg-[#fafafa] transition-colors ${wasUnlocked ? 'bg-[#fffbeb]' : ''}`}>
                      <td className="px-4 py-3 text-sm text-[#111111] font-medium">
                        <div className="flex flex-col gap-1.5">
                          <Input
                            type="text"
                            value={item.name}
                            onChange={(e) => onFieldChange(item.id, 'name', e.target.value)}
                            className="w-full min-w-[220px] border-[#e5e5e5] bg-white text-sm text-[#111111] focus:border-[#737373] focus:ring-1 focus:ring-[#737373]"
                          />
                          {(() => {
                            const page = parseSourcePage(item.sourcePageOrCell);
                            if (page === null) return null;
                            return onJumpToPage ? (
                              <button
                                type="button"
                                onClick={() => onJumpToPage(page)}
                                title={`Jump to page ${page} in the source document`}
                                className="mt-1 inline-flex w-fit items-center rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-inset ring-slate-300/60 hover:bg-slate-200 hover:ring-slate-400"
                              >
                                Page {page}
                              </button>
                            ) : (
                              <span className="mt-1 inline-flex w-fit items-center rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-inset ring-slate-300/60">
                                Page {page}
                              </span>
                            );
                          })()}
                          {wasUnlocked ? (
                            <span className="text-[10px] text-amber-600 font-medium">
                              Unlocked — stays in reserve bucket; won't change operating total
                            </span>
                          ) : null}
                          {mergedLabel ? (
                            <span
                              title={mergedTooltip}
                              className="inline-flex w-fit rounded-full border border-[#d4d4d4] bg-[#fafafa] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[#525252]"
                            >
                              {mergedLabel}
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <input
                          type="number"
                          step="any"
                          value={item.ytdActual}
                          onChange={(e) => onFieldChange(item.id, 'ytdActual', e.target.value)}
                          className="w-28 rounded-md border border-[#e5e5e5] bg-white px-2 py-1.5 text-right text-sm font-mono text-[#525252] focus:border-[#737373] focus:outline-none focus:ring-1 focus:ring-[#737373] [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                        />
                      </td>
                      <td className="px-4 py-3 text-right">
                        <input
                          type="number"
                          step="any"
                          value={item.annualBudget}
                          onChange={(e) => onFieldChange(item.id, 'annualBudget', e.target.value)}
                          className="w-28 rounded-md border border-[#e5e5e5] bg-white px-2 py-1.5 text-right text-sm font-mono text-[#525252] focus:border-[#737373] focus:outline-none focus:ring-1 focus:ring-[#737373] [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                        />
                      </td>
                      <td className="px-6 py-4 text-sm text-[#737373] text-right font-mono">
                        {percentDiff.toFixed(1)}%
                      </td>
                      <td className="px-4 py-3 text-right">
                        <input
                          type="number"
                          step="any"
                          value={item.projection ?? 0}
                          onChange={(e) => onFieldChange(item.id, 'projection', e.target.value)}
                          className="w-28 rounded-md border border-[#e5e5e5] bg-white px-2 py-1.5 text-right text-sm font-mono text-[#525252] focus:border-[#737373] focus:outline-none focus:ring-1 focus:ring-[#737373] [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                        />
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="flex flex-col gap-1">
                          <div className="flex items-center rounded-md border border-[#d4d4d4] bg-white overflow-hidden focus-within:border-[#737373] focus-within:ring-1 focus-within:ring-[#737373]">
                            {inputMode[item.id] === 'dollar' ? (
                              <input
                                type="number"
                                step="100"
                                value={Math.round(proposedChange)}
                                onChange={(e) => handleDollarChangeInput(item.id, e.target.value, item.annualBudget)}
                                className="w-20 text-right text-sm h-8 px-2 bg-transparent border-0 outline-none font-mono [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                              />
                            ) : (
                              <input
                                type="number"
                                step="0.1"
                                value={item.percentChange}
                                onChange={(e) => handlePercentChangeInput(item.id, e.target.value)}
                                className="w-16 text-right text-sm h-8 px-2 bg-transparent border-0 outline-none font-mono [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                              />
                            )}
                            <button
                              onClick={() => toggleInputMode(item.id)}
                              className="h-8 px-2.5 border-l border-[#d4d4d4] bg-[#f5f5f5] text-[#525252] hover:bg-[#e5e5e5] transition-colors flex items-center select-none shrink-0 text-xs font-medium"
                              title={inputMode[item.id] === 'dollar' ? 'Switch to percentage' : 'Switch to dollar amount'}
                            >
                              {inputMode[item.id] === 'dollar' ? '$' : '%'}
                            </button>
                          </div>
                          {item.percentChange !== 0 && (
                            <div className="text-[10px] text-[#a3a3a3] text-right font-mono mt-0.5">
                              {inputMode[item.id] === 'dollar' ? (
                                <span>{item.percentChange > 0 ? '+' : ''}{item.percentChange.toFixed(1)}%</span>
                              ) : (
                                <span>{proposedChange >= item.annualBudget ? '+' : ''}{formatCurrency(proposedChange - item.annualBudget)}</span>
                              )}
                            </div>
                          )}
                        </div>
                      </td>
                      <td className="px-6 py-4 text-sm font-semibold text-[#111111] text-right font-mono">
                        {formatCurrency(proposedChange)}
                      </td>
                      <td className="px-6 py-4 text-sm text-[#737373] text-right font-mono">
                        {formatCurrency(monthly)}
                      </td>
                      <td className="px-6 py-4 text-center">
                        <button
                          onClick={() => toggleNote(item.id)}
                          className={`p-2 rounded transition-colors ${
                            item.note ? 'hover:bg-[#dbeafe] text-[#2563eb]' : 'hover:bg-[#f5f5f5] text-[#a3a3a3]'
                          }`}
                        >
                          <MessageSquare
                            className={`w-4 h-4 ${
                              item.note ? 'fill-[#2563eb]' : ''
                            }`}
                          />
                        </button>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="flex flex-col gap-1.5 items-end">
                          {wasUnlocked && onReadOnlyOverride ? (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => onReadOnlyOverride(item.id, null)}
                              className="border-amber-300 text-amber-700 hover:bg-amber-50 gap-1"
                              title="Re-lock this line — return to read-only reserve default"
                            >
                              <Lock className="w-3 h-3" />
                              Lock
                            </Button>
                          ) : (
                            <Button
                              type="button"
                              variant="outline"
                              size="icon"
                              onClick={() => onRequestMerge(item.id)}
                              className="h-8 w-8 border-[#d4d4d4] text-[#525252] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
                              title="Merge with another row"
                            >
                              <GitMerge className="w-3.5 h-3.5" />
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>,
                    // Note Expansion
                    ...(isNoteExpanded ? [
                      <tr key={`${item.id}-note`}>
                        <td colSpan={10} className="bg-[#fafafa] px-6 py-6 border-b border-[#e5e5e5]">
                          <div className="space-y-3 max-w-4xl">
                            <Input
                              placeholder="Note Title"
                              value={noteEdits[item.id]?.title || ''}
                              onChange={(e) =>
                                setNoteEdits((prev) => ({
                                  ...prev,
                                  [item.id]: { ...prev[item.id], title: e.target.value, body: prev[item.id]?.body || '' },
                                }))
                              }
                              className="bg-white border-[#d4d4d4] text-sm focus:border-[#737373] focus:ring-1 focus:ring-[#737373]"
                            />
                            <textarea
                              placeholder="Add detailed notes, assumptions, or justifications..."
                              value={noteEdits[item.id]?.body || ''}
                              onChange={(e) =>
                                setNoteEdits((prev) => ({
                                  ...prev,
                                  [item.id]: { ...prev[item.id], title: prev[item.id]?.title || '', body: e.target.value },
                                }))
                              }
                              className="w-full min-h-24 p-3 bg-white border border-[#d4d4d4] rounded-lg text-sm text-[#111111] placeholder:text-[#a3a3a3] resize-y focus:border-[#737373] focus:ring-1 focus:ring-[#737373]"
                            />
                            <div className="flex gap-2">
                              <Button
                                onClick={() => void saveNote(item.id)}
                                disabled={savingNoteId === item.id}
                                className="bg-[#111111] text-white hover:bg-[#262626] shadow-sm"
                                size="sm"
                              >
                                {savingNoteId === item.id ? 'Saving...' : 'Save Note'}
                              </Button>
                              <Button
                                onClick={() => setExpandedNote(null)}
                                variant="outline"
                                className="border-[#d4d4d4] hover:bg-[#f5f5f5]"
                                size="sm"
                              >
                                Cancel
                              </Button>
                            </div>
                          </div>
                        </td>
                      </tr>
                    ] : [])
                  ];
                }),
                // Category Total — per-category subtotal row. Passes includeReadOnly=true
                // so read-only reserve categories sum their real values instead of
                // collapsing to $0. proposedChange/monthly stay 0 for read-only items
                // via calcDisplayProposed (read-only items have no proposed changes).
                <tr key={`${category}-total`} className="bg-[#f5f5f5] font-semibold border-b-2 border-[#d4d4d4]">
                  <td className="px-6 py-4 text-sm text-[#111111]">{getCategoryLabel(category)} Total</td>
                  <td className="px-6 py-4 text-sm text-[#111111] text-right font-mono">
                    {formatCurrency(calcDisplayCategoryTotal(items, 'ytdActual', reserveInflationRate, true))}
                  </td>
                  <td className="px-6 py-4 text-sm text-[#111111] text-right font-mono">
                    {formatCurrency(calcDisplayCategoryTotal(items, 'annualBudget', reserveInflationRate, true))}
                  </td>
                  <td className="px-6 py-4 text-sm text-[#737373] text-right">—</td>
                  <td className="px-6 py-4 text-sm text-[#111111] text-right font-mono">
                    {formatCurrency(calcDisplayCategoryTotal(items, 'projection', reserveInflationRate, true))}
                  </td>
                  <td className="px-6 py-4 text-sm text-[#737373] text-right">—</td>
                  <td className="px-6 py-4 text-sm font-bold text-[#111111] text-right font-mono">
                    {formatCurrency(calcDisplayCategoryTotal(items, 'proposedChange', reserveInflationRate, true))}
                  </td>
                  <td className="px-6 py-4 text-sm text-[#737373] text-right font-mono">
                    {formatCurrency(calcDisplayCategoryTotal(items, 'monthly', reserveInflationRate, true))}
                  </td>
                  <td className="px-6 py-4"></td>
                  <td className="px-6 py-4"></td>
                </tr>
              ])}
            </tbody>
          </table>
        </div>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-3 gap-6">
        <div className="bg-white border border-[#e5e5e5] rounded-lg p-6 shadow-sm">
          <div className="text-xs font-medium text-[#737373] mb-2 uppercase tracking-wide">Total Annual Budget</div>
          <div className="text-3xl font-semibold text-[#111111]">{formatCurrency(totalAnnualBudget)}</div>
          <div className="text-xs text-[#a3a3a3] mt-1">Operating + reserve expenses</div>
        </div>
        <div className="bg-white border border-[#e5e5e5] rounded-lg p-6 shadow-sm">
          <div className="text-xs font-medium text-[#737373] mb-2 uppercase tracking-wide">Monthly Total</div>
          <div className="text-3xl font-semibold text-[#111111]">{formatCurrency(totalAnnualBudget / 12)}</div>
        </div>
        <div className="bg-white border border-[#e5e5e5] rounded-lg p-6 shadow-sm">
          <div className="text-xs font-medium text-[#737373] mb-2 uppercase tracking-wide">Per Unit Monthly</div>
          <div className="text-3xl font-semibold text-[#111111]">
            {perUnitMonthly == null ? '—' : formatCurrency(perUnitMonthly)}
          </div>
        </div>
      </div>

      {/* Profit / Loss Summary */}
      <div className="bg-white border border-[#e5e5e5] rounded-lg shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-[#e5e5e5] bg-[#fafafa]">
          <span className="text-xs font-semibold text-[#525252] uppercase tracking-wide">Income vs. Expense Summary</span>
        </div>
        <div className="grid grid-cols-3 divide-x divide-[#e5e5e5]">
          <div className="px-6 py-4">
            <div className="text-xs font-medium text-[#737373] mb-1 uppercase tracking-wide">Total Income</div>
            <div className="text-2xl font-semibold text-[#16a34a]">{formatCurrency(totalIncome)}</div>
          </div>
          <div className="px-6 py-4">
            <div className="text-xs font-medium text-[#737373] mb-1 uppercase tracking-wide">Total Expense</div>
            <div className="text-2xl font-semibold text-[#dc2626]">{formatCurrency(totalAnnualBudget)}</div>
          </div>
          <div className="px-6 py-4">
            <div className="text-xs font-medium text-[#737373] mb-1 uppercase tracking-wide">
              {netSurplusDeficit > 0 ? 'Surplus' : netSurplusDeficit < 0 ? 'Deficit' : 'Balanced'}
            </div>
            <div className={`text-2xl font-semibold ${
              netSurplusDeficit > 0
                ? 'text-[#16a34a]'
                : netSurplusDeficit < 0
                  ? 'text-[#dc2626]'
                  : 'text-[#111111]'
            }`}>
              {netSurplusDeficit === 0
                ? formatCurrency(0)
                : `${netSurplusDeficit > 0 ? '+' : ''}${formatCurrency(netSurplusDeficit)}`}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
