import { useState } from 'react';
import { MessageSquare, ChevronDown, ChevronUp, Percent, DollarSign } from 'lucide-react';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { type LineItem } from '../data/mockData';

interface EnrichedViewProps {
  lineItems: LineItem[];
  onPercentChange: (itemId: string, newPercent: number) => void;
  onNoteUpdate: (itemId: string, title: string, body: string) => void;
  units: number;
}

export function EnrichedView({ lineItems, onPercentChange, onNoteUpdate, units }: EnrichedViewProps) {
  const [expandedNote, setExpandedNote] = useState<string | null>(null);
  const [noteEdits, setNoteEdits] = useState<Record<string, { title: string; body: string }>>({});
  const [calculating, setCalculating] = useState(false);
  const [operatingIncomePercent, setOperatingIncomePercent] = useState(0);
  const [incomeHeaderPercent, setIncomeHeaderPercent] = useState(0);
  const [inputMode, setInputMode] = useState<Record<string, 'percent' | 'dollar'>>({});

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(value);
  };

  const calculatePercentDifference = (ytd: number, budget: number) => {
    if (ytd === 0) return 0;
    return ((budget - ytd) / ytd) * 100;
  };

  const calculateProjection = (ytd: number) => {
    // Extrapolate YTD to full year (assuming 8 months of data)
    return (ytd / 8) * 12;
  };

  const calculateProposedChange = (projection: number, percentChange: number) => {
    return projection * (1 + percentChange / 100);
  };

  const calculateMonthly = (proposedChange: number) => {
    return proposedChange / 12;
  };

  const handlePercentChangeInput = (itemId: string, value: string) => {
    const numValue = parseFloat(value) || 0;
    setCalculating(true);
    onPercentChange(itemId, numValue);
    setTimeout(() => setCalculating(false), 300);
  };

  const handleDollarChangeInput = (itemId: string, dollarValue: string, projection: number) => {
    const numValue = parseFloat(dollarValue) || 0;
    const percentChange = projection > 0 ? ((numValue - projection) / projection) * 100 : 0;
    setCalculating(true);
    onPercentChange(itemId, percentChange);
    setTimeout(() => setCalculating(false), 300);
  };

  const toggleInputMode = (itemId: string) => {
    setInputMode(prev => ({
      ...prev,
      [itemId]: prev[itemId] === 'dollar' ? 'percent' : 'dollar'
    }));
  };

  const handleOperatingIncomeChange = (value: string) => {
    const numValue = parseFloat(value) || 0;
    setCalculating(true);
    setOperatingIncomePercent(numValue);
    setTimeout(() => setCalculating(false), 300);
  };

  const handleIncomeHeaderChange = (value: string) => {
    const numValue = parseFloat(value) || 0;
    setCalculating(true);
    setIncomeHeaderPercent(numValue);
    setTimeout(() => setCalculating(false), 300);
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

  const saveNote = (itemId: string) => {
    const edit = noteEdits[itemId];
    if (edit) {
      onNoteUpdate(itemId, edit.title, edit.body);
    }
  };

  const getCategoryLabel = (category: string) => {
    switch (category) {
      case 'income':
        return 'INCOME';
      case 'operating':
        return 'OPERATING EXPENSES';
      case 'reserve':
        return 'RESERVE CONTRIBUTIONS';
      default:
        return category.toUpperCase();
    }
  };

  const groupedItems = lineItems.reduce((acc, item) => {
    if (!acc[item.category]) {
      acc[item.category] = [];
    }
    acc[item.category].push(item);
    return acc;
  }, {} as Record<string, LineItem[]>);

  // Calculate totals
  const calculateCategoryTotal = (items: LineItem[], field: 'ytdActual' | 'annualBudget' | 'projection' | 'proposedChange' | 'monthly') => {
    return items.reduce((sum, item) => {
      if (field === 'projection') {
        return sum + calculateProjection(item.ytdActual);
      } else if (field === 'proposedChange') {
        const projection = calculateProjection(item.ytdActual);
        return sum + calculateProposedChange(projection, item.percentChange);
      } else if (field === 'monthly') {
        const projection = calculateProjection(item.ytdActual);
        const proposedChange = calculateProposedChange(projection, item.percentChange);
        return sum + calculateMonthly(proposedChange);
      }
      return sum + item[field];
    }, 0);
  };

  // Total Annual Budget
  const totalAnnualBudget = lineItems.reduce((sum, item) => {
    const projection = calculateProjection(item.ytdActual);
    return sum + calculateProposedChange(projection, item.percentChange);
  }, 0);

  const perUnitMonthly = totalAnnualBudget / 12 / units;

  return (
    <div className="space-y-8">
      {/* Calculation Status */}
      {calculating && (
        <div className="fixed top-20 right-8 bg-[#111111] text-white px-4 py-2.5 rounded-lg text-sm shadow-lg z-50">
          Recalculating...
        </div>
      )}

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
              </tr>
            </thead>
            <tbody>
              {Object.entries(groupedItems).flatMap(([category, items]) => [
                // Operating Income Row (only for income category, before header)
                ...(category === 'income' ? [
                  <tr key="operating-income" className="border-b border-[#e5e5e5] hover:bg-[#fafafa] transition-colors">
                    <td className="px-6 py-4 text-sm text-[#111111] font-medium">Operating Income</td>
                    <td className="px-6 py-4 text-sm text-[#525252] text-right font-mono">
                      0.00
                    </td>
                    <td className="px-6 py-4 text-sm text-[#525252] text-right font-mono">
                      0.00
                    </td>
                    <td className="px-6 py-4 text-sm text-[#737373] text-right font-mono">
                      0.0
                    </td>
                    <td className="px-6 py-4 text-sm text-[#525252] text-right font-mono">
                      0.00
                    </td>
                    <td className="px-6 py-4 text-right">
                      <Input
                        type="number"
                        step="0.1"
                        value={operatingIncomePercent}
                        onChange={(e) => handleOperatingIncomeChange(e.target.value)}
                        className="w-24 text-right text-sm h-9 bg-white border-[#d4d4d4] focus:border-[#737373] focus:ring-1 focus:ring-[#737373] font-mono [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                      />
                    </td>
                    <td className="px-6 py-4 text-sm font-semibold text-[#111111] text-right font-mono">
                      0.00
                    </td>
                    <td className="px-6 py-4 text-sm text-[#737373] text-right font-mono">
                      0.00
                    </td>
                    <td className="px-6 py-4 text-center">
                      <button
                        disabled
                        className="p-2 rounded transition-colors text-[#d4d4d4] cursor-not-allowed"
                      >
                        <MessageSquare className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ] : []),
                // Category Header
                ...(category === 'income' ? [
                  <tr key={`${category}-header`} className="bg-[#f5f5f5] border-b border-[#e5e5e5]">
                    <td className="px-6 py-3.5 text-xs font-bold text-[#111111] uppercase tracking-wide">
                      {getCategoryLabel(category)}
                    </td>
                    <td className="px-6 py-3.5 text-xs font-bold text-[#111111] text-right font-mono">
                      0.00
                    </td>
                    <td className="px-6 py-3.5 text-xs font-bold text-[#111111] text-right font-mono">
                      0.00
                    </td>
                    <td className="px-6 py-3.5 text-xs font-bold text-[#111111] text-right font-mono">
                      0.0
                    </td>
                    <td className="px-6 py-3.5 text-xs font-bold text-[#111111] text-right font-mono">
                      0.00
                    </td>
                    <td className="px-6 py-3.5 text-right">
                      <Input
                        type="number"
                        step="0.1"
                        value={incomeHeaderPercent}
                        onChange={(e) => handleIncomeHeaderChange(e.target.value)}
                        className="w-24 text-right text-xs h-8 bg-white border-[#d4d4d4] focus:border-[#737373] focus:ring-1 focus:ring-[#737373] font-mono font-bold [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                      />
                    </td>
                    <td className="px-6 py-3.5 text-xs font-bold text-[#111111] text-right font-mono">
                      0.00
                    </td>
                    <td className="px-6 py-3.5 text-xs font-bold text-[#111111] text-right font-mono">
                      0.00
                    </td>
                    <td className="px-6 py-3.5"></td>
                  </tr>
                ] : [
                  <tr key={`${category}-header`} className="bg-[#f5f5f5] border-b border-[#e5e5e5]">
                    <td colSpan={9} className="px-6 py-3.5 text-xs font-bold text-[#111111] uppercase tracking-wide">
                      {getCategoryLabel(category)}
                    </td>
                  </tr>
                ]),
                // Line Items
                ...items.flatMap((item) => {
                  const percentDiff = calculatePercentDifference(item.ytdActual, item.annualBudget);
                  const projection = calculateProjection(item.ytdActual);
                  const proposedChange = calculateProposedChange(projection, item.percentChange);
                  const monthly = calculateMonthly(proposedChange);
                  const isNoteExpanded = expandedNote === item.id;

                  return [
                    <tr key={item.id} className="border-b border-[#e5e5e5] hover:bg-[#fafafa] transition-colors">
                      <td className="px-6 py-4 text-sm text-[#111111] font-medium">{item.name}</td>
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
                          <div className="relative flex items-center gap-1">
                            {inputMode[item.id] === 'dollar' ? (
                              <>
                                <span className="absolute left-3 text-xs text-[#737373] pointer-events-none">$</span>
                                <Input
                                  type="number"
                                  step="100"
                                  value={Math.round(proposedChange)}
                                  onChange={(e) => handleDollarChangeInput(item.id, e.target.value, projection)}
                                  className="w-32 pl-6 text-right text-sm h-9 bg-white border-[#d4d4d4] focus:border-[#737373] focus:ring-1 focus:ring-[#737373] font-mono [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                                />
                                <button
                                  onClick={() => toggleInputMode(item.id)}
                                  className="p-2 rounded bg-[#f5f5f5] text-[#737373] hover:bg-[#e5e5e5] hover:text-[#525252] transition-colors"
                                  title="Switch to percentage"
                                >
                                  <DollarSign className="w-3.5 h-3.5" />
                                </button>
                              </>
                            ) : (
                              <>
                                <Input
                                  type="number"
                                  step="0.1"
                                  value={item.percentChange}
                                  onChange={(e) => handlePercentChangeInput(item.id, e.target.value)}
                                  className="w-24 text-right text-sm h-9 bg-white border-[#d4d4d4] focus:border-[#737373] focus:ring-1 focus:ring-[#737373] font-mono [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                                />
                                <span className="text-xs text-[#737373] ml-0.5">%</span>
                                <button
                                  onClick={() => toggleInputMode(item.id)}
                                  className="p-2 rounded bg-[#f5f5f5] text-[#737373] hover:bg-[#e5e5e5] hover:text-[#525252] transition-colors"
                                  title="Switch to dollar amount"
                                >
                                  <Percent className="w-3.5 h-3.5" />
                                </button>
                              </>
                            )}
                          </div>
                          {/* Display both values */}
                          {item.percentChange !== 0 && (
                            <div className="text-[10px] text-[#737373] text-right font-mono">
                              {inputMode[item.id] === 'dollar' ? (
                                <span>{item.percentChange > 0 ? '+' : ''}{item.percentChange.toFixed(1)}%</span>
                              ) : (
                                <span>{proposedChange > projection ? '+' : ''}{formatCurrency(proposedChange - projection)}</span>
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
                    </tr>,
                    // Note Expansion
                    ...(isNoteExpanded ? [
                      <tr key={`${item.id}-note`}>
                        <td colSpan={9} className="bg-[#fafafa] px-6 py-6 border-b border-[#e5e5e5]">
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
                                onClick={() => saveNote(item.id)}
                                className="bg-[#111111] text-white hover:bg-[#262626] shadow-sm"
                                size="sm"
                              >
                                Save Note
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
                // Category Total
                <tr key={`${category}-total`} className="bg-[#f5f5f5] font-semibold border-b-2 border-[#d4d4d4]">
                  <td className="px-6 py-4 text-sm text-[#111111]">{getCategoryLabel(category)} Total</td>
                  <td className="px-6 py-4 text-sm text-[#111111] text-right font-mono">
                    {formatCurrency(calculateCategoryTotal(items, 'ytdActual'))}
                  </td>
                  <td className="px-6 py-4 text-sm text-[#111111] text-right font-mono">
                    {formatCurrency(calculateCategoryTotal(items, 'annualBudget'))}
                  </td>
                  <td className="px-6 py-4 text-sm text-[#737373] text-right">—</td>
                  <td className="px-6 py-4 text-sm text-[#111111] text-right font-mono">
                    {formatCurrency(calculateCategoryTotal(items, 'projection'))}
                  </td>
                  <td className="px-6 py-4 text-sm text-[#737373] text-right">—</td>
                  <td className="px-6 py-4 text-sm font-bold text-[#111111] text-right font-mono">
                    {formatCurrency(calculateCategoryTotal(items, 'proposedChange'))}
                  </td>
                  <td className="px-6 py-4 text-sm text-[#737373] text-right font-mono">
                    {formatCurrency(calculateCategoryTotal(items, 'monthly'))}
                  </td>
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
          <div className="text-3xl font-semibold text-[#111111]">{formatCurrency(perUnitMonthly)}</div>
        </div>
      </div>
    </div>
  );
}