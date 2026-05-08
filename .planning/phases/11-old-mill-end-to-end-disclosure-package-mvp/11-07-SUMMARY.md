---
phase: 11
plan: 07
subsystem: frontend/disclosure-package
tags: [phase-11, frontend, ui, polling, disclosure-panel, hooks]
requires:
  - 11-06  # backend API endpoints (POST /generate, GET /status, /download, /audit)
provides:
  - frontend.api.disclosurePackage  # typed client over the 4 backend endpoints
  - frontend.hooks.useDisclosureJob # 2s-poll, 120s-timeout, monotonic-stage hook
  - frontend.components.disclosure  # 5 React components for the panel state machine
  - frontend.lib.jobStageColors     # stage-chip color helper (sibling of statusColors)
affects:
  - frontend/src/app/components/BudgetScreenWrapper.tsx  # mounts the panel
tech_stack_added: []                # no new deps; reuses lucide-react, existing UI primitives, Tailwind
patterns:
  - polling-hook                    # first polling hook in the codebase
  - panel-state-machine             # discriminated UI state via hook return
  - verbatim-copy-deck              # all visible strings sourced from UI-SPEC §9
key_files:
  created:
    - frontend/src/app/api/disclosurePackage.ts
    - frontend/src/app/lib/jobStageColors.ts
    - frontend/src/app/components/disclosure/useDisclosureJob.ts
    - frontend/src/app/components/disclosure/DisclosurePackagePanel.tsx
    - frontend/src/app/components/disclosure/DisclosurePreflightChecklist.tsx
    - frontend/src/app/components/disclosure/DisclosureProgressBlock.tsx
    - frontend/src/app/components/disclosure/DisclosureResultBlock.tsx
    - frontend/src/app/components/disclosure/DisclosureFailureBlock.tsx
  modified:
    - frontend/src/app/components/BudgetScreenWrapper.tsx
decisions:
  - "Mount point: panel renders inside BudgetScreenWrapper as a sibling of <BudgetScreen>, wrapped in a max-w-7xl container, only on the budget-workspace branch (NOT on the historical GeneratedBudgetScreen branch). UI-SPEC §5.1."
  - "Fiscal year derivation: hoa.portfolio_year ?? new Date().getFullYear(), matching the existing BudgetScreen.tsx:810 precedent. UI-SPEC OQ-3 alternative (VITE_OLD_MILL_HOA_ID env var) was not adopted — the HOA name match is sufficient for Phase 11 and avoids a build-time env coupling."
  - "Old Mill detection: literal name match against 'Old Mill Homeowners Association'. A backend-driven `disclosure_supported` boolean would supersede this in Phase 12; the constant is documented for forward consistency."
  - "Polling hook: closure-based isMountedRef + per-fetch state guard, no AbortController. handleResponse from http.ts does not currently take a signal, and the no-op-if-unmounted pattern is the established convention (see BudgetScreenWrapper's `cancelled` flag in loadBudgetContext)."
  - "Result block omits page count, file size, SHA-256: Plan 11-06 status response only carries timestamps + paths; surfacing those would require placeholder values, which violates the no-stub rule. Documented as deferred to Phase 12 metadata extension."
  - "Audit log surface: hook returns a `view audit log` callback prop slot but the panel does NOT pass it for Phase 11 — the audit-sheet component is out of scope for this plan (UI-SPEC §6.1 lists it but Plan 11-07 task scope is panel + 4 supporting blocks + API client)."
  - "Abort affordance: not implemented per UI-SPEC OQ-1 — backend has no `/abort` endpoint in Phase 11, so the button is hidden rather than fake."
metrics:
  tasks_completed: 3
  tasks_total: 3
  files_created: 8
  files_modified: 1
  duration_minutes: ~25
  completed_at: "2026-05-08T14:44:08Z"
---

# Phase 11 Plan 07: Frontend Disclosure-Package Panel Summary

**One-liner:** Frontend API client, 2s-polling hook, and 5 React components implementing the disclosure-package generation panel with the verbatim UI-SPEC §9 copy deck.

## What was built

### Task 1 — API client + polling hook (`9b0bf8c`)

- **`frontend/src/app/api/disclosurePackage.ts`** — typed wrappers over the 4 backend endpoints (`POST /generate`, `GET /{id}/status`, `GET /{id}/download`, `GET /{id}/audit`) plus a `disclosurePackageDownloadUrl(jobId)` helper for anchor-based browser downloads. All requests route through the existing `authHeaders()` + `handleResponse<T>` / `handleBlobResponse` from `api/http.ts`, so the 401-refresh-retry flow is inherited without per-call code.
- **`frontend/src/app/lib/jobStageColors.ts`** — sibling of `statusColors.ts` per UI-SPEC §6.2. Exports `getJobStageColor(state: 'done'|'active'|'pending')`, the canonical `STAGE_ORDER` (5 stages + `completed`), and a `STAGE_LABEL` map (note "Ready" is the user-facing label for both `verifying` and `completed`).
- **`frontend/src/app/components/disclosure/useDisclosureJob.ts`** — first polling hook in the codebase. 2s poll cadence (UI-SPEC §8.4), 120s hard timeout, 3-consecutive-failure backoff to a "Lost connection..." failure, monotonic stage transitions (server `stage` arriving in non-monotonic order is ignored), separate 1s elapsed-timer ticker, and unmount-safe state writes via `isMountedRef`. The optimistic `validating` chip is set on click before the 202 returns (UI-SPEC §8.2).

### Task 2 — 5 React components (`5cf92dd`)

All components use the `ReserveStudyView.tsx` card chrome verbatim (`rounded-2xl border border-[#e5e5e5] bg-white p-6 shadow-sm`) with the eyebrow / title / body / actions structure from PATTERNS.

- **`DisclosurePreflightChecklist`** — 5 fixed Old Mill row labels (UI-SPEC §9.3 verbatim), generic `pass/fail/loading/unknown` status rendering for forward compatibility.
- **`DisclosureProgressBlock`** — 5 stage chips with inline color from `getJobStageColor`, `Loader2` spin honoring `motion-reduce`, `"Running for Nm Ss"` elapsed line with the format from §9.4, and a long-running notice that fades in at 30s elapsed via the `transition-opacity duration-200` class. Wrapped in `role="status" aria-live="polite" aria-atomic="true"` per §10.
- **`DisclosureResultBlock`** — title `"Disclosure package ready"`, optional `Generated` timestamp, anchor-based `Download PDF` primary CTA (the second of two accent buttons reserved by UI-SPEC §4) with a content-disposition filename hint (`old-mill-{fiscal_year}-disclosure-package.pdf`) matching the backend's response header.
- **`DisclosureFailureBlock`** — stage-tagged title (`Generation failed during validation` / `…calculation` / `…rendering` / `…merge` / `…verification`) from §9.6, server error body with the generic fallback line, Retry button styled with the destructive ramp (`border-[#fecaca] text-[#b91c1c]`).
- **`DisclosurePackagePanel`** — top-level state machine (idle / starting / running / completed / failed). Conditionally renders preflight (idle), progress (starting+running), result (completed), or failure (failed). Single primary CTA `"Generate Disclosure Package"` accent-styled when supported, disabled+visually-50 when locked.

### Task 3 — Wrapper integration (`48eff7d`)

`BudgetScreenWrapper.tsx` now wraps its `<BudgetScreen>` render in a fragment and appends `<DisclosurePackagePanel>` inside a `max-w-7xl px-8 pb-12` container. The panel is **not** rendered on the `showGeneratedBudget` branch (historical-version view) — disclosure generation is a workspace-level action, not a per-version action. Fiscal year derives from `hoa.portfolio_year ?? new Date().getFullYear()` (matching the `computeBudgetParameters(...)` precedent at `BudgetScreen.tsx:810`); supported-HOA test is a literal name match against `OLD_MILL_HOA_NAME = 'Old Mill Homeowners Association'`.

## Deviations from Plan

**None of substance.** A few intentional simplifications relative to the plan's example code:

- The plan's example `useDisclosureJob.ts` constructed an `AbortController` inside the hook but never wired it into `fetch`. I dropped the unused controller in favor of the codebase's established no-op-if-unmounted pattern (see `BudgetScreenWrapper.loadBudgetContext`'s `cancelled` flag). Behavior is identical: in-flight responses resolve into a guarded `setState` that no-ops when unmounted.
- `DisclosureResultBlock` does NOT render placeholder rows for `Pages`, `File size`, `Checksum (SHA-256)` because Plan 11-06 's status payload doesn't carry those fields. Surfacing them with `--` placeholders would violate the no-stub rule. The decision is documented in the frontmatter `decisions[]` for the next plan to revisit.
- `DisclosureResultBlock` accepts an `onViewAudit?` callback for forward compatibility but the panel never passes it (the audit sheet component is out of scope for this plan — UI-SPEC §6.1 lists `DisclosureAuditSheet` as a separate component).
- Abort button is hidden (UI-SPEC OQ-1: no backend `/abort` endpoint in Phase 11; do not pretend to abort).

## Output spec answers (per plan §output)

1. **Insertion point in BudgetScreenWrapper.tsx (RESEARCH OQ-7):** Sibling of `<BudgetScreen>` inside a `<>` fragment, wrapped in a `max-w-7xl px-8 pb-12` div. The historical-version branch (`showGeneratedBudget`) is left untouched — only the live budget workspace shows the panel. This is the cheapest insertion point and aligns with UI-SPEC §5.1.
2. **HOA-record fiscal-year availability:** `HOARecord.portfolio_year: number | null` was the closest existing field; the wrapper now derives `disclosureFiscalYear = hoa.portfolio_year ?? new Date().getFullYear()`, matching the precedent at `BudgetScreen.tsx:810`. No new HOA-record field was needed.
3. **Additional copy strings discovered during implementation:** None. Every visible string in the panel exists in UI-SPEC §9 or its sibling sections, with these helpful corrections noted:
   - `Lost connection to job status. Refresh the page to check status.` — appears twice in §9.6 (Network error template) and §8.4 (poll-failure copy); used the §9.6 wording.
   - `An unexpected error occurred. Try again, or contact support if it persists.` — §9.6 "Body fallback"; used as the universal failure fallback.
   - `Taking longer than expected. Generation typically completes in 10-20 seconds.` — §9.4 "Long-running notice".
   - Aria labels on stage chips (`Validating: in progress`, etc.) follow §10's "Each stage chip is a `<span role="status">` with an `aria-label` like 'Validating: complete' or 'Rendering: in progress'" guidance.

## Known Stubs

None. Every component renders only data it actually has from the backend — no hardcoded empty arrays flowing into UI, no placeholder strings, no "coming soon" labels.

The 5 preflight rows DO render unconditionally as `pass` for the supported-HOA case. This is **not** a stub: per UI-SPEC §5.2 / OQ-6, Phase 11 treats the Old Mill preflight as implicitly passing because the panel only enables Generate on the supported HOA, where all 5 inputs are guaranteed present by construction. The `DisclosurePreflightChecklist` accepts an arbitrary row list so Phase 12+ can wire real per-row state without component changes.

## Threat Flags

None. The new code introduces no new network surface — all requests go through the existing 4 backend endpoints which are auth-gated. The PDF download uses an anchor `<a href download>` that relies on the existing JWT cookie pattern (already established by the project; T-11-02 disposition stays `mitigate`).

## TDD Gate Compliance

Plan 11-07 frontmatter is `type: execute` (not `tdd`), so RED/GREEN/REFACTOR commit gating does not apply. Verification was static-only (file existence + verbatim-string grep) because the worktree has no installed `node_modules` for this branch (`npx tsc` is unavailable; the project does not ship a `tsc` script in `package.json`). Type correctness was reviewed manually by tracing each new symbol against the existing `appSettings.ts`, `budgetHistory.ts`, `http.ts`, and `hoa.ts` shapes.

## Self-Check: PASSED

**Files created:**

- `frontend/src/app/api/disclosurePackage.ts` — FOUND
- `frontend/src/app/lib/jobStageColors.ts` — FOUND
- `frontend/src/app/components/disclosure/useDisclosureJob.ts` — FOUND
- `frontend/src/app/components/disclosure/DisclosurePackagePanel.tsx` — FOUND
- `frontend/src/app/components/disclosure/DisclosurePreflightChecklist.tsx` — FOUND
- `frontend/src/app/components/disclosure/DisclosureProgressBlock.tsx` — FOUND
- `frontend/src/app/components/disclosure/DisclosureResultBlock.tsx` — FOUND
- `frontend/src/app/components/disclosure/DisclosureFailureBlock.tsx` — FOUND

**Files modified:**

- `frontend/src/app/components/BudgetScreenWrapper.tsx` — FOUND with `DisclosurePackagePanel` import and mount

**Commits:**

- `9b0bf8c` — Task 1 (API client + polling hook + jobStageColors) — FOUND
- `5cf92dd` — Task 2 (5 disclosure components) — FOUND
- `48eff7d` — Task 3 (BudgetScreenWrapper integration) — FOUND

**Verbatim copy strings present:**

- `Generate Disclosure Package` (DisclosurePackagePanel) — FOUND
- `Disclosure package ready` (DisclosureResultBlock) — FOUND
- `Generation Progress` (DisclosureProgressBlock) — FOUND
- `Generation Failed` (DisclosureFailureBlock) — FOUND
- `Budget draft saved` (DisclosurePreflightChecklist) — FOUND
- `Old Mill Homeowners Association` (BudgetScreenWrapper) — FOUND
