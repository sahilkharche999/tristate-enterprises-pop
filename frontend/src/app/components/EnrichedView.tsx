import { useState } from 'react';
import { MessageSquare, ChevronDown, Percent, DollarSign } from 'lucide-react';
import { toast } from 'sonner';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { saveBudgetNote } from '../api/budgetHistory';
import { type LineItem } from '../data/mockData';
import { mergedBadgeLabel, mergedBadgeTooltip } from '../lib/glMerge.ts';
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
  isReserveComponent,
} from '../lib/budget';
import { getErrorMessage } from '../lib/errors';

interface EnrichedViewProps {
  hoaId: string;
  draftId: number | null;
  lineItems: LineItem[];
  onPercentChange: (itemId: string, newPercent: number) => void;
  onNoteSaved: (itemId: string, title: string, body: string) => void;
  onRequestMerge: (itemId: string) => void;
  units: number;
  reserveInflationRate: number;
}

export function EnrichedView({
  hoaId,
  draftId,
  lineItems,
  onPercentChange,
  onNoteSaved,
  onRequestMerge,
  units,
  reserveInflationRate,
}: EnrichedViewProps) {
  const [expandedNote, setExpandedNote] = useState<string | null>(null);
  const [noteEdits, setNoteEdits] = useState<Record<string, { title: string; body: string }>>({});
  const [inputMode, setInputMode] = useState<Record<string, 'percent' | 'dollar'>>({});
  const [savingNoteId, setSavingNoteId] = useState<string | null>(null);


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

  // Total Annual Budget = expenses only (operating + reserve expenses), not reserve income
  const totalAnnualBudget =
    calcDisplayCategoryTotal(groupedItems['operating'] || [], 'proposedChange', reserveInflationRate) +
    calcDisplayCategoryTotal(groupedItems['reserve'] || [], 'proposedChange', reserveInflationRate) +
    calcDisplayCategoryTotal(groupedItems['reserve_expense'] || [], 'proposedChange', reserveInflationRate);

  const perUnitMonthly = units > 0 ? totalAnnualBudget / 12 / units : null;

  return (
    <div className="space-y-8">
      {/* Table */}
      <div className="bg-white border border-[#e5e5e5] rounded-lg overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-[#fafafa] border-b border-[#e5e5e5]">
              <tr>
                <th className="text-left px-6 py-4 text-xs font-semibold text-[#525252] uppercase tracking-wider">Line Item</th>
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
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              disabled
                              className="border-[#e5e5e5] text-[#a3a3a3]"
                            >
                              Merge with...
                            </Button>
                          </td>
                        </tr>,
                      ];
                    }
                    return [
                      <tr key={item.id} className="border-b border-[#e5e5e5] opacity-60">
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
                        <td className="px-6 py-4 text-sm text-[#a3a3a3] text-right text-xs">Excluded</td>
                        <td className="px-6 py-4 text-sm text-[#a3a3a3] text-right">—</td>
                        <td className="px-6 py-4 text-sm text-[#a3a3a3] text-right">—</td>
                        <td className="px-6 py-4 text-center">
                          <button disabled className="p-2 rounded text-[#d4d4d4] cursor-not-allowed">
                            <MessageSquare className="w-4 h-4" />
                          </button>
                        </td>
                        <td className="px-6 py-4 text-right">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            disabled
                            className="border-[#e5e5e5] text-[#a3a3a3]"
                          >
                            Merge with...
                          </Button>
                        </td>
                      </tr>,
                    ];
                  }

                  const projection = item.projection ?? 0;
                  const proposedChange = calcProposed(item.annualBudget, item.percentChange);
                  const monthly = calcMonthly(proposedChange);
                  const percentDiff = calcPercentDiff(projection, item.annualBudget);

                  return [
                    <tr key={item.id} className="border-b border-[#e5e5e5] hover:bg-[#fafafa] transition-colors">
                      <td className="px-6 py-4 text-sm text-[#111111] font-medium">
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
                      <td className="px-6 py-4 text-sm text-[#525252] text-right font-mono">
                        {formatCurrency(item.ytdActual)}
                      </td>
                      <td className="px-6 py-4 text-sm text-[#525252] text-right font-mono">
                        {formatCurrency(item.annualBudget)}
                      </td>
                      <td className="px-6 py-4 text-sm text-[#737373] text-right font-mono">
                        {percentDiff.toFixed(1)}%
                      </td>
                      <td className="px-6 py-4 text-sm text-[#525252] text-right font-mono">
                        {formatCurrency(projection)}
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
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => onRequestMerge(item.id)}
                          className="border-[#d4d4d4] text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
                        >
                          Merge with...
                        </Button>
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
    </div>
  );
}
