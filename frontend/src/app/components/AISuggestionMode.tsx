import { useState } from 'react';
import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
import { type AISuggestion, type LineItem } from '../data/mockData';

interface AISuggestionModeProps {
  suggestions: AISuggestion[];
  lineItems: LineItem[];
  onApply: (selectedSuggestions: AISuggestion[]) => void;
}

export function AISuggestionMode({ suggestions, lineItems, onApply }: AISuggestionModeProps) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const toggleSelection = (id: string) => {
    setSelectedIds((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(id)) {
        newSet.delete(id);
      } else {
        newSet.add(id);
      }
      return newSet;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === suggestions.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(suggestions.map((s) => s.lineItemId)));
    }
  };

  const handleApply = () => {
    const selected = suggestions.filter((s) => selectedIds.has(s.lineItemId));
    onApply(selected);
  };

  const handleClear = () => {
    setSelectedIds(new Set());
  };

  const getCurrentPercent = (itemId: string) => {
    const item = lineItems.find((i) => i.id === itemId);
    return item?.percentChange || 0;
  };

  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 90) return 'text-green-700';
    if (confidence >= 80) return 'text-yellow-700';
    return 'text-orange-700';
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-medium text-[#111111]">AI Suggested % Change</h2>
          <p className="text-sm text-[#666666] mt-1">
            Review AI-generated suggestions based on historical data, market trends, and industry benchmarks
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button
            onClick={toggleSelectAll}
            variant="outline"
            className="border-[#E5E5E5] text-[#111111]"
          >
            {selectedIds.size === suggestions.length ? 'Deselect All' : 'Select All'}
          </Button>
          <Button
            onClick={handleClear}
            variant="outline"
            className="border-[#E5E5E5] text-[#111111]"
          >
            Clear
          </Button>
          <Button
            onClick={handleApply}
            disabled={selectedIds.size === 0}
            className="bg-[#000000] text-white hover:bg-[#111111]"
          >
            Apply Selected ({selectedIds.size})
          </Button>
        </div>
      </div>

      {/* Table */}
      <div className="bg-white border border-[#E5E5E5] rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-[#F7F7F7] border-b border-[#E5E5E5]">
              <tr>
                <th className="w-12 px-6 py-4"></th>
                <th className="text-left px-6 py-4 text-sm font-medium text-[#111111]">Line Item</th>
                <th className="text-right px-6 py-4 text-sm font-medium text-[#111111]">Current %</th>
                <th className="text-right px-6 py-4 text-sm font-medium text-[#111111]">Suggested %</th>
                <th className="text-right px-6 py-4 text-sm font-medium text-[#111111]">Delta %</th>
                <th className="text-right px-6 py-4 text-sm font-medium text-[#111111]">Confidence</th>
                <th className="text-left px-6 py-4 text-sm font-medium text-[#111111]">Reason</th>
              </tr>
            </thead>
            <tbody>
              {suggestions.map((suggestion) => {
                const isSelected = selectedIds.has(suggestion.lineItemId);
                const currentPercent = getCurrentPercent(suggestion.lineItemId);
                const delta = suggestion.suggestedPercent - currentPercent;

                return (
                  <tr
                    key={suggestion.lineItemId}
                    className={`border-b border-[#E5E5E5] ${isSelected ? 'bg-[#FAFAFA]' : 'hover:bg-[#FAFAFA]'}`}
                  >
                    <td className="px-6 py-4">
                      <Checkbox
                        checked={isSelected}
                        onCheckedChange={() => toggleSelection(suggestion.lineItemId)}
                      />
                    </td>
                    <td className="px-6 py-4 text-sm text-[#111111] font-medium">
                      {suggestion.lineItemName}
                    </td>
                    <td className="px-6 py-4 text-sm text-[#666666] text-right">
                      {currentPercent.toFixed(1)}%
                    </td>
                    <td className="px-6 py-4 text-sm font-medium text-[#111111] text-right">
                      {suggestion.suggestedPercent.toFixed(1)}%
                    </td>
                    <td className="px-6 py-4 text-sm text-[#666666] text-right">
                      {delta > 0 ? '+' : ''}
                      {delta.toFixed(1)}%
                    </td>
                    <td className={`px-6 py-4 text-sm font-medium text-right ${getConfidenceColor(suggestion.confidence)}`}>
                      {suggestion.confidence}%
                    </td>
                    <td className="px-6 py-4">
                      <p className="text-sm text-[#111111] leading-relaxed">
                        {suggestion.reason}
                      </p>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Info Box */}
      <div className="bg-[#F7F7F7] border border-[#E5E5E5] rounded-lg p-6">
        <h4 className="text-sm font-medium text-[#111111] mb-2">How AI Suggestions Work</h4>
        <ul className="text-sm text-[#666666] space-y-1">
          <li>• Suggestions are generated using historical data analysis, inflation trends, and market benchmarks</li>
          <li>• Confidence scores reflect data quality and consistency of trend patterns</li>
          <li>• Review each suggestion and apply selectively based on your HOA's specific circumstances</li>
          <li>• Applied suggestions will update the % Change column in the Enriched View</li>
        </ul>
      </div>
    </div>
  );
}