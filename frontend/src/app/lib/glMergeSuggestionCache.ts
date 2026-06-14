import type { BudgetGlMergeSuggestionPayload } from '../api/budgetHistory.ts';

interface GlMergeSuggestionCache {
  suggestions: BudgetGlMergeSuggestionPayload[];
  dismissedKeys: string[];
}

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

const EMPTY_CACHE: GlMergeSuggestionCache = {
  suggestions: [],
  dismissedKeys: [],
};

export function glMergeSuggestionStorageKey(
  hoaId: number | string,
  draftId: number | string,
): string {
  return `budget-gl-merge-suggestions:${hoaId}:${draftId}`;
}

export function readGlMergeSuggestionCache(
  storage: StorageLike,
  hoaId: number | string,
  draftId: number | string,
): GlMergeSuggestionCache {
  const raw = storage.getItem(glMergeSuggestionStorageKey(hoaId, draftId));
  if (!raw) {
    return EMPTY_CACHE;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<GlMergeSuggestionCache> | null;
    return {
      suggestions: Array.isArray(parsed?.suggestions) ? parsed!.suggestions : [],
      dismissedKeys: Array.isArray(parsed?.dismissedKeys) ? parsed!.dismissedKeys : [],
    };
  } catch {
    return EMPTY_CACHE;
  }
}

export function writeGlMergeSuggestionCache(
  storage: StorageLike,
  hoaId: number | string,
  draftId: number | string,
  cache: GlMergeSuggestionCache,
): void {
  storage.setItem(
    glMergeSuggestionStorageKey(hoaId, draftId),
    JSON.stringify(cache),
  );
}
