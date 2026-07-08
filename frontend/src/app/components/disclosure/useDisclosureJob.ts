// Polling hook for the disclosure-package generation flow.
//
// UI-SPEC §8.4 polling discipline:
//   - Poll at 2s interval, do not throttle on tab inactive (jobs are short).
//   - 120s hard timeout — surface a "lost connection" failure if the terminal
//     state has not been seen by then.
//   - 3 consecutive poll failures → same "lost connection" failure.
//   - Cancel polling and abort in-flight fetches on unmount (no leak).
//   - Never regress backwards through the stage list (defense-in-depth against
//     non-monotonic stage values from the server).
//
// UI-SPEC §8.2: optimistic stage = `validating` immediately after click; all
// subsequent stages must come from the polled status.

import { useCallback, useEffect, useRef, useState } from 'react';

import type {
  DisclosurePackageJob,
  DisclosurePackageStage,
} from '../../api/disclosurePackage';
import {
  generateDisclosurePackage,
  getDisclosurePackageStatus,
} from '../../api/disclosurePackage';
import { STAGE_ORDER } from '../../lib/jobStageColors';

const POLL_INTERVAL_MS = 2000;
const HARD_TIMEOUT_MS = 120_000;
const MAX_CONSECUTIVE_FAILURES = 3;

export type DisclosureJobUiState =
  | 'idle'
  | 'starting'
  | 'running'
  | 'completed'
  | 'failed';

export interface UseDisclosureJobValue {
  state: DisclosureJobUiState;
  job: DisclosurePackageJob | null;
  stage: DisclosurePackageStage;
  elapsedMs: number;
  error: string | null;
  generate: (hoaId: number, fiscalYear: number) => Promise<void>;
  reset: () => void;
}

const LOST_CONNECTION_MESSAGE =
  'Lost connection to job status. Refresh the page to check status.';
const FALLBACK_FAILURE_MESSAGE =
  'An unexpected error occurred. Try again, or contact support if it persists.';

function stageIndex(stage: DisclosurePackageStage | string | null | undefined): number {
  if (!stage) return -1;
  return STAGE_ORDER.indexOf(stage as (typeof STAGE_ORDER)[number]);
}

export function useDisclosureJob(): UseDisclosureJobValue {
  const [state, setState] = useState<DisclosureJobUiState>('idle');
  const [job, setJob] = useState<DisclosurePackageJob | null>(null);
  const [stage, setStage] = useState<DisclosurePackageStage>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const startedAtRef = useRef<number | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const consecutiveFailuresRef = useRef(0);
  const isPollingRef = useRef(false);
  const isMountedRef = useRef(true);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    if (elapsedTimerRef.current) {
      clearInterval(elapsedTimerRef.current);
      elapsedTimerRef.current = null;
    }
    isPollingRef.current = false;
  }, []);

  const reset = useCallback(() => {
    stopPolling();
    if (!isMountedRef.current) return;
    setState('idle');
    setJob(null);
    setStage(null);
    setElapsedMs(0);
    setError(null);
    consecutiveFailuresRef.current = 0;
    startedAtRef.current = null;
  }, [stopPolling]);

  const generate = useCallback(
    // C1: packageId targets a specific annual package; a finalized target
    // with valid frozen snapshots renders from the snapshots, not live state.
    async (hoaId: number, fiscalYear: number, packageId?: number) => {
      // Reset to a clean baseline (UI-SPEC §7.1: panel transitions through
      // running → terminal; clicking Generate from `failed` re-enters running).
      stopPolling();
      consecutiveFailuresRef.current = 0;
      startedAtRef.current = Date.now();
      if (isMountedRef.current) {
        setError(null);
        setJob(null);
        setElapsedMs(0);
        setState('starting');
        // Optimistic per UI-SPEC §8.2.
        setStage('validating');
      }

      let initial: DisclosurePackageJob;
      try {
        initial = await generateDisclosurePackage(hoaId, fiscalYear, packageId);
      } catch (err) {
        if (!isMountedRef.current) return;
        const message =
          err && typeof err === 'object' && 'message' in err
            ? String((err as { message: unknown }).message ?? '')
            : err instanceof Error
              ? err.message
              : String(err);
        setState('failed');
        setError(message || FALLBACK_FAILURE_MESSAGE);
        return;
      }

      if (!isMountedRef.current) return;
      setJob(initial);
      setState('running');
      // The 202 response carries `status` but not `stage`; keep the optimistic
      // `validating` chip until a real polled stage arrives.

      // Elapsed-time ticker (1s cadence — independent of poll interval).
      elapsedTimerRef.current = setInterval(() => {
        if (!isMountedRef.current) return;
        if (startedAtRef.current) {
          setElapsedMs(Date.now() - startedAtRef.current);
        }
      }, 1000);

      const tick = async () => {
        if (!isMountedRef.current || isPollingRef.current) return;
        // Hard timeout (UI-SPEC §8.4).
        if (
          startedAtRef.current &&
          Date.now() - startedAtRef.current > HARD_TIMEOUT_MS
        ) {
          stopPolling();
          if (!isMountedRef.current) return;
          setState('failed');
          setError(LOST_CONNECTION_MESSAGE);
          return;
        }

        isPollingRef.current = true;
        try {
          const next = await getDisclosurePackageStatus(initial.id);
          if (!isMountedRef.current) return;
          consecutiveFailuresRef.current = 0;
          setJob(next);
          // Never regress backwards (UI-SPEC §8.2).
          if (next.stage) {
            setStage((prev) => {
              const prevIdx = stageIndex(prev);
              const nextIdx = stageIndex(next.stage);
              return nextIdx > prevIdx ? next.stage ?? prev : prev;
            });
          }
          if (next.status === 'completed') {
            stopPolling();
            setState('completed');
          } else if (next.status === 'failed') {
            stopPolling();
            setState('failed');
            setError(next.error_message ?? FALLBACK_FAILURE_MESSAGE);
          }
        } catch {
          if (!isMountedRef.current) return;
          consecutiveFailuresRef.current += 1;
          if (consecutiveFailuresRef.current >= MAX_CONSECUTIVE_FAILURES) {
            stopPolling();
            setState('failed');
            setError(LOST_CONNECTION_MESSAGE);
          }
        } finally {
          isPollingRef.current = false;
        }
      };

      pollTimerRef.current = setInterval(() => {
        void tick();
      }, POLL_INTERVAL_MS);
    },
    [stopPolling],
  );

  // Cancel polling on unmount. NOTE: any in-flight fetch will resolve into a
  // no-op because the per-call closure checks `isMountedRef.current` before
  // touching state. We do not abort the underlying fetch with AbortController
  // because `handleResponse` does not currently take a signal — the no-op
  // pattern is the established convention in this codebase (see
  // BudgetScreenWrapper.tsx `cancelled` flag).
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      stopPolling();
    };
  }, [stopPolling]);

  return { state, job, stage, elapsedMs, error, generate, reset };
}
