import { useState } from 'react';
import { AlertTriangle, ChevronDown, Sparkles, Undo2 } from 'lucide-react';

import type {
  BudgetGlMergeListItem,
  BudgetGlMergeSuggestionPayload,
} from '../api/budgetHistory.ts';
import { Button } from './ui/button';

interface GLMergeSuggestionsProps {
  suggestions: BudgetGlMergeSuggestionPayload[];
  merges: BudgetGlMergeListItem[];
  loading: boolean;
  error: string | null;
  actionLoading: boolean;
  unmergingApplicationId: number | null;
  onSuggest: () => void;
  onApplySuggestion: (suggestion: BudgetGlMergeSuggestionPayload) => void;
  onModifySuggestion: (suggestion: BudgetGlMergeSuggestionPayload) => void;
  onDismissSuggestion: (suggestion: BudgetGlMergeSuggestionPayload) => void;
  onUnmerge: (merge: BudgetGlMergeListItem) => void;
}

function confidenceTone(confidence: number) {
  if (confidence >= 0.9) {
    return 'border-emerald-200 bg-emerald-50 text-emerald-900';
  }
  if (confidence >= 0.7) {
    return 'border-amber-200 bg-amber-50 text-amber-900';
  }
  return 'border-orange-200 bg-orange-50 text-orange-900';
}

export function GLMergeSuggestions({
  suggestions,
  merges,
  loading,
  error,
  actionLoading,
  unmergingApplicationId,
  onSuggest,
  onApplySuggestion,
  onModifySuggestion,
  onDismissSuggestion,
  onUnmerge,
}: GLMergeSuggestionsProps) {
  const [isOpen, setIsOpen] = useState(false);
  const appliedMerges = merges.filter((merge) => merge.application_status === 'applied');
  const pendingCount = suggestions.length + appliedMerges.length;

  return (
    <section className="space-y-4">
      <div className="rounded-2xl border border-[#e5e5e5] bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <button
            type="button"
            onClick={() => setIsOpen((open) => !open)}
            className="flex items-start gap-3 text-left"
            aria-expanded={isOpen}
          >
            <ChevronDown
              className={`mt-1 h-4 w-4 shrink-0 text-[#737373] transition-transform ${isOpen ? 'rotate-180' : ''}`}
            />
            <div className="space-y-2">
              <p className="text-xs font-medium uppercase tracking-[0.2em] text-[#737373]">
                Similar Rows
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-xl font-semibold text-[#111111]">GL Merge Review</h2>
                {!isOpen && pendingCount > 0 ? (
                  <span className="rounded-full bg-[#f5f5f5] px-2.5 py-0.5 text-xs font-medium text-[#525252]">
                    {pendingCount}
                  </span>
                ) : null}
              </div>
              {isOpen ? (
                <p className="max-w-3xl text-sm text-[#666666]">
                  Combine duplicate or near-duplicate budget rows before mapping. Merges rewrite the
                  active draft and lock when you generate a new immutable version.
                </p>
              ) : null}
            </div>
          </button>
          <Button
            type="button"
            onClick={onSuggest}
            disabled={loading || actionLoading}
            className="bg-[#111111] text-white shadow-sm hover:bg-[#262626]"
          >
            <Sparkles className="mr-2 h-4 w-4" />
            {loading ? 'Finding similar rows...' : 'Suggest Merges'}
          </Button>
        </div>

        {isOpen && appliedMerges.length > 0 ? (
          <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium text-amber-900">
              <AlertTriangle className="h-4 w-4" />
              {appliedMerges.length} applied merge{appliedMerges.length === 1 ? '' : 's'} will
              lock when this draft becomes a generated version.
            </div>
            <div className="space-y-2">
              {appliedMerges.map((merge) => (
                <div
                  key={`${merge.id}-${merge.application_id ?? 'draft'}`}
                  className="flex flex-col gap-3 rounded-lg border border-amber-200 bg-white px-4 py-3 md:flex-row md:items-center md:justify-between"
                >
                  <div>
                    <p className="text-sm font-medium text-[#111111]">
                      {merge.primary_label} absorbs {merge.secondary_label}
                    </p>
                    <p className="text-xs text-[#737373]">
                      {merge.source === 'auto_applied' ? 'Auto-applied from prior upload' : 'Applied in this draft'}
                    </p>
                  </div>
                  {merge.application_id ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => onUnmerge(merge)}
                      disabled={unmergingApplicationId === merge.application_id}
                      className="border-[#d4d4d4] text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
                    >
                      <Undo2 className="mr-2 h-4 w-4" />
                      {unmergingApplicationId === merge.application_id
                        ? 'Reverting...'
                        : merge.source === 'auto_applied'
                          ? 'Revert to Separate'
                          : 'Unmerge'}
                    </Button>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {isOpen && error ? (
          <div className="mt-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
            {error}
          </div>
        ) : null}

        {isOpen ? (
        <div className="mt-5 space-y-3">
          {suggestions.length === 0 ? (
            <div className="rounded-xl border border-dashed border-[#d4d4d4] bg-[#fafafa] px-4 py-5 text-sm text-[#737373]">
              {loading
                ? 'Checking the active draft for merge candidates...'
                : 'No merge suggestions yet. Run “Suggest Merges” or use “Merge with...” on a row.'}
            </div>
          ) : (
            suggestions.map((suggestion) => (
              <div
                key={`${suggestion.primary_account_code ?? suggestion.primary_normalized_label}-${suggestion.secondary_account_code ?? suggestion.secondary_normalized_label}`}
                className="rounded-xl border border-[#e5e5e5] bg-[#fafafa] p-4"
              >
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-semibold text-[#111111]">
                        {suggestion.primary_label} + {suggestion.secondary_label}
                      </p>
                      <span
                        className={`rounded-full border px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide ${confidenceTone(suggestion.confidence)}`}
                      >
                        {(suggestion.confidence * 100).toFixed(0)}% confidence
                      </span>
                      {suggestion.local_only ? (
                        <span className="rounded-full border border-[#d4d4d4] bg-white px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide text-[#525252]">
                          Local only
                        </span>
                      ) : null}
                    </div>
                    <p className="text-sm text-[#666666]">{suggestion.reason}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      size="sm"
                      onClick={() => onApplySuggestion(suggestion)}
                      disabled={actionLoading}
                      className="bg-[#111111] text-white hover:bg-[#262626]"
                    >
                      Apply
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => onModifySuggestion(suggestion)}
                      disabled={actionLoading}
                      className="border-[#d4d4d4] text-[#111111] hover:border-[#a3a3a3] hover:bg-[#f5f5f5]"
                    >
                      Modify
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => onDismissSuggestion(suggestion)}
                      disabled={actionLoading}
                      className="text-[#525252] hover:bg-[#f5f5f5]"
                    >
                      Dismiss
                    </Button>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
        ) : null}
      </div>
    </section>
  );
}
