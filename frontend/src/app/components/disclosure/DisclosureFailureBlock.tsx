// Failure block (UI-SPEC §6.1, §7.5, §9.6).
//
// Renders only when the panel is in the `failed` state. Title is stage-tagged
// per UI-SPEC §9.6; the body is the verbatim server error message, falling
// back to the generic line when the server returned no detail.

import { Button } from '../ui/button';

const STAGE_TITLES: Record<string, string> = {
  // UI-SPEC §9.6 — verbatim.
  validating: 'Generation failed during validation',
  computing: 'Generation failed during calculation',
  rendering: 'Generation failed during rendering',
  merging: 'Generation failed during merge',
  verifying: 'Generation failed during verification',
};

const FALLBACK_BODY =
  'An unexpected error occurred. Try again, or contact support if it persists.';

export interface DisclosureFailureBlockProps {
  errorMessage: string;
  stage?: string | null;
  onRetry?: () => void;
}

export function DisclosureFailureBlock({
  errorMessage,
  stage,
  onRetry,
}: DisclosureFailureBlockProps) {
  const title = (stage && STAGE_TITLES[stage]) || 'Generation failed';
  const body = errorMessage || FALLBACK_BODY;

  return (
    <div
      role="alert"
      className="rounded-xl border border-[#fecaca] bg-[#fef2f2] p-6 transition-opacity duration-200"
    >
      <p className="text-xs font-medium uppercase tracking-[0.2em] text-[#b91c1c]">
        Generation Failed
      </p>
      <h3 className="mt-1 text-lg font-semibold text-[#b91c1c]">{title}</h3>
      <p className="mt-3 text-sm text-[#404040]">{body}</p>
      {onRetry ? (
        <div className="mt-4 flex gap-2">
          <Button
            onClick={onRetry}
            variant="outline"
            className="border-[#fecaca] text-[#b91c1c] hover:bg-[#fee2e2]"
          >
            Retry generation
          </Button>
        </div>
      ) : null}
    </div>
  );
}
