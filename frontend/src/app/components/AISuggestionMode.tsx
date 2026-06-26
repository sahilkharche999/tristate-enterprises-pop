import { useMemo, useState } from 'react';
import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
import { submitAIFeedback } from '../api/macros';
import {
  type AISuggestion,
  type AISuggestionResponse,
  type FeedbackDecision,
  type LineItem,
} from '../data/mockData';

interface AISuggestionModeProps {
  aiResponse: AISuggestionResponse | null;
  lineItems: LineItem[];
  loading: boolean;
  error: string | null;
  onApply: (selectedSuggestions: AISuggestion[]) => void;
  onRefetch: () => void;
}

function CoherenceBadge({ score }: { score: 'high' | 'medium' | 'low' }) {
  const colors = {
    high: 'bg-green-100 text-green-800 border-green-200',
    medium: 'bg-yellow-100 text-yellow-800 border-yellow-200',
    low: 'bg-red-100 text-red-800 border-red-200',
  };
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${colors[score]}`}>
      {score.charAt(0).toUpperCase() + score.slice(1)} Coherence
    </span>
  );
}

export function AISuggestionMode({
  aiResponse,
  lineItems,
  loading,
  error,
  onApply,
  onRefetch,
}: AISuggestionModeProps) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [rowDecisions, setRowDecisions] = useState<Record<string, 'accepted' | 'modified' | 'rejected'>>({});
  const [submitting, setSubmitting] = useState(false);
  const [expandedReasons, setExpandedReasons] = useState<Set<string>>(new Set());

  // Map backend suggestions to frontend AISuggestion shape, keyed by LineItem.id via accountCode
  const suggestions: AISuggestion[] = useMemo(() => {
    if (!aiResponse) return [];
    const accountCodeToItemId = new Map<number, string>(
      lineItems.filter((i) => i.accountCode != null).map((i) => [i.accountCode!, i.id])
    );
    return aiResponse.suggestions.map((s) => {
      const itemId = accountCodeToItemId.get(s.account_code) ?? String(s.id);
      const lineItem = lineItems.find((i) => i.id === itemId);
      return {
        lineItemId: itemId,
        lineItemName: s.account_name,
        currentPercent: lineItem?.percentChange ?? 0,
        suggestedPercent: s.suggested_pct_change * 100,
        confidence: s.confidence * 100,
        reason: s.reason,
        revisedByPass2: s.revised_by_pass2,
        cbrMatch: s.cbr_match,
        mlBaseline: s.ml_baseline,
        feedbackCaseId: s.id,
      };
    });
  }, [aiResponse, lineItems]);

  const toggleSelection = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelectedIds(
      selectedIds.size === suggestions.length ? new Set() : new Set(suggestions.map((s) => s.lineItemId))
    );
  };

  const handleRowDecision = (id: string, decision: 'accepted' | 'rejected') => {
    setRowDecisions((prev) => ({ ...prev, [id]: decision }));
    if (decision === 'rejected') {
      setSelectedIds((prev) => { const s = new Set(prev); s.delete(id); return s; });
    } else {
      setSelectedIds((prev) => new Set([...prev, id]));
    }
  };

  const handleApply = async () => {
    const selected = suggestions.filter((s) => selectedIds.has(s.lineItemId));
    if (aiResponse?.run_id) {
      setSubmitting(true);
      try {
        // Send ALL decisions (accepted + rejected) so the backend learns from both
        const decisions: FeedbackDecision[] = suggestions
          .filter((s) => rowDecisions[s.lineItemId] === 'accepted' || rowDecisions[s.lineItemId] === 'rejected')
          .map((s) => ({
            feedbackCaseId: s.feedbackCaseId ?? 0,
            decision: rowDecisions[s.lineItemId] ?? 'accepted',
            finalPctChange: rowDecisions[s.lineItemId] === 'rejected' ? 0 : s.suggestedPercent / 100,
          }));
        if (decisions.length > 0) {
          await submitAIFeedback({ runId: aiResponse.run_id, decisions });
        }
      } catch {
        // Non-blocking — apply even if feedback fails
      } finally {
        setSubmitting(false);
      }
    }
    onApply(selected);
  };

  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 90) return 'text-green-700';
    if (confidence >= 75) return 'text-yellow-700';
    return 'text-orange-700';
  };

  if (loading) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-xl font-medium text-[#111111]">AI Suggested % Change</h2>
          <p className="text-sm text-[#666666] mt-1">Analyzing line items with 5-stage AI pipeline...</p>
        </div>
        <div className="bg-white border border-[#E5E5E5] rounded-lg p-12 flex flex-col items-center gap-4">
          <div className="w-10 h-10 border-2 border-[#111111] border-t-transparent rounded-full animate-spin" />
          <div className="text-center">
            <p className="text-sm font-medium text-[#111111]">
              Analyzing ~{lineItems.filter((i) => !i.readOnly && i.accountCode).length} items...
            </p>
            <p className="text-xs text-[#666666] mt-1">Typically 3–5 seconds</p>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        <h2 className="text-xl font-medium text-[#111111]">AI Suggested % Change</h2>
        <div className="bg-red-50 border border-red-200 rounded-lg p-8 text-center">
          <p className="text-sm font-medium text-red-800 mb-2">AI Service Unavailable</p>
          <p className="text-sm text-red-600 mb-4">{error}</p>
          <Button onClick={onRefetch} variant="outline" className="border-red-300 text-red-700 hover:bg-red-50">
            Retry
          </Button>
        </div>
      </div>
    );
  }

  if (!aiResponse) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-medium text-[#111111]">AI Suggested % Change</h2>
          <Button onClick={onRefetch} className="bg-[#000000] text-white hover:bg-[#111111]">
            Generate AI Suggestions
          </Button>
        </div>
        <div className="bg-white border border-[#E5E5E5] rounded-lg p-12 text-center">
          <p className="text-sm text-[#666666]">
            Click "Generate AI Suggestions" to analyze your budget line items.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-medium text-[#111111]">AI Suggested % Change</h2>
          <p className="text-sm text-[#666666] mt-1">
            Review AI-generated suggestions based on historical data, seasonality, and market context
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button onClick={onRefetch} variant="outline" className="border-[#E5E5E5] text-[#111111]">
            Refresh
          </Button>
          <Button onClick={toggleSelectAll} variant="outline" className="border-[#E5E5E5] text-[#111111]">
            {selectedIds.size === suggestions.length ? 'Deselect All' : 'Select All'}
          </Button>
          <Button
            onClick={handleApply}
            disabled={selectedIds.size === 0 || submitting}
            className="bg-[#000000] text-white hover:bg-[#111111]"
          >
            {submitting ? 'Applying...' : `Apply Selected (${selectedIds.size})`}
          </Button>
        </div>
      </div>

      {/* Executive Summary Card */}
      <div className="bg-white border border-[#E5E5E5] rounded-lg p-6">
        <div className="flex items-start gap-4">
          <div className="flex-1">
            <div className="flex items-center gap-3 mb-3">
              <h3 className="text-sm font-semibold text-[#111111]">Executive Summary</h3>
              <CoherenceBadge score={aiResponse.coherence_score} />
              <span className="text-sm text-[#666666]">{aiResponse.total_budget_impact}</span>
            </div>
            <p className="text-sm text-[#525252] leading-relaxed">{aiResponse.executive_summary}</p>
          </div>
        </div>
        {aiResponse.flagged_items.length > 0 && (
          <div className="mt-4 pt-4 border-t border-[#F0F0F0]">
            <p className="text-xs font-medium text-[#111111] mb-2">
              Flagged &amp; Revised ({aiResponse.flagged_items.length})
            </p>
            <div className="space-y-1">
              {aiResponse.flagged_items.map((f) => (
                <div key={f.account_code} className="text-xs text-[#666666]">
                  <span className="font-medium text-[#111111]">{f.account_code}</span>: {f.issue}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Assessment Recommendation Card */}
      {aiResponse.recommended_assessment_increase_pct > 0 ? (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-6">
          <div className="flex items-start gap-3">
            <div className="flex-1">
              <div className="flex items-center gap-3 mb-2">
                <h3 className="text-sm font-semibold text-amber-900">Assessment Increase Recommendation</h3>
                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800 border border-amber-300">
                  +{(aiResponse.recommended_assessment_increase_pct * 100).toFixed(1)}% recommended
                </span>
              </div>
              <p className="text-sm text-amber-800 leading-relaxed">
                {aiResponse.assessment_recommendation_note}
              </p>
              <p className="text-xs text-amber-700 mt-2">
                This estimate is based on the suggested expense increases above. Review and adjust as needed before applying.
              </p>
            </div>
          </div>
        </div>
      ) : aiResponse.assessment_recommendation_note ? (
        <div className="bg-green-50 border border-green-200 rounded-lg p-4">
          <p className="text-sm text-green-800">{aiResponse.assessment_recommendation_note}</p>
        </div>
      ) : null}

      {/* Suggestions Table */}
      <div className="bg-white border border-[#E5E5E5] rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-[#F7F7F7] border-b border-[#E5E5E5]">
              <tr>
                <th className="w-12 px-4 py-4" />
                <th className="text-left px-4 py-4 text-sm font-medium text-[#111111]">Line Item</th>
                <th className="text-right px-4 py-4 text-sm font-medium text-[#111111]">Current %</th>
                <th className="text-right px-4 py-4 text-sm font-medium text-[#111111]">Suggested %</th>
                <th className="text-right px-4 py-4 text-sm font-medium text-[#111111]">Confidence</th>
                <th className="text-left px-4 py-4 text-sm font-medium text-[#111111]">Reason</th>
                <th className="text-center px-4 py-4 text-sm font-medium text-[#111111]">Decision</th>
              </tr>
            </thead>
            <tbody>
              {suggestions.map((suggestion) => {
                const isSelected = selectedIds.has(suggestion.lineItemId);
                const decision = rowDecisions[suggestion.lineItemId];

                return (
                  <tr
                    key={suggestion.lineItemId}
                    className={`border-b border-[#E5E5E5] ${isSelected ? 'bg-[#FAFAFA]' : 'hover:bg-[#FAFAFA]'}`}
                  >
                    <td className="px-4 py-4">
                      <Checkbox
                        checked={isSelected}
                        onCheckedChange={() => toggleSelection(suggestion.lineItemId)}
                      />
                    </td>
                    <td className="px-4 py-4 text-sm text-[#111111] font-medium">
                      <div className="flex items-center gap-2">
                        {suggestion.lineItemName}
                        {suggestion.revisedByPass2 && (
                          <span className="text-xs bg-purple-100 text-purple-700 px-1.5 py-0.5 rounded border border-purple-200">
                            Pass 2 ↺
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-4 text-sm text-[#666666] text-right">
                      {suggestion.currentPercent.toFixed(1)}%
                    </td>
                    <td className="px-4 py-4 text-sm font-medium text-[#111111] text-right">
                      {suggestion.suggestedPercent.toFixed(1)}%
                    </td>
                    <td className={`px-4 py-4 text-sm font-medium text-right ${getConfidenceColor(suggestion.confidence)}`}>
                      {suggestion.confidence.toFixed(0)}%
                    </td>
                    <td
                      className="px-4 py-4 max-w-xs cursor-pointer"
                      onClick={() => setExpandedReasons(prev => {
                        const next = new Set(prev);
                        if (next.has(suggestion.lineItemId)) next.delete(suggestion.lineItemId);
                        else next.add(suggestion.lineItemId);
                        return next;
                      })}
                    >
                      <p className={`text-sm text-[#111111] leading-relaxed ${expandedReasons.has(suggestion.lineItemId) ? '' : 'line-clamp-3'}`}>
                        {suggestion.reason}
                      </p>
                    </td>
                    <td className="px-4 py-4">
                      <div className="flex items-center gap-1 justify-center">
                        <button
                          onClick={() => handleRowDecision(suggestion.lineItemId, 'accepted')}
                          className={`px-2 py-1 text-xs rounded border transition-colors ${
                            decision === 'accepted'
                              ? 'bg-green-100 border-green-400 text-green-800 font-medium'
                              : 'border-[#E5E5E5] text-[#666666] hover:bg-green-50'
                          }`}
                        >
                          ✓
                        </button>
                        <button
                          onClick={() => handleRowDecision(suggestion.lineItemId, 'rejected')}
                          className={`px-2 py-1 text-xs rounded border transition-colors ${
                            decision === 'rejected'
                              ? 'bg-red-100 border-red-400 text-red-800 font-medium'
                              : 'border-[#E5E5E5] text-[#666666] hover:bg-red-50'
                          }`}
                        >
                          ✗
                        </button>
                      </div>
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
          <li>• 5-stage pipeline: Feature Engineering → CBR → CatBoost → LLM Pass 1 → LLM Pass 2</li>
          <li>• Pass 2 holistic review flags anomalies and ensures budget coherence across all items</li>
          <li>• Confidence scores reflect CBR similarity, ML/LLM alignment, and historical data quality</li>
          <li>• Your accept/reject decisions improve future suggestions via case-based learning</li>
        </ul>
      </div>
    </div>
  );
}
