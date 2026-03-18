# Codebase Concerns

**Analysis Date:** 2026-03-18

## Tech Debt

**Mixed real and mock product state:**
- Issue: Auth, workbook processing, and AI suggestions are wired to the backend, but HOA catalog data, settings flows, sync history, and parts of the knowledge-base workflow still come from `frontend/src/app/data/mockData.ts`, `frontend/src/app/components/SyncHistoryScreen.tsx`, and `frontend/src/app/components/SettingsSelector.tsx`
- Why: The frontend prototype was integrated incrementally instead of being fully re-platformed at once
- Impact: Users can authenticate into a system that still uses fake HOA metadata and non-persistent workflow screens
- Fix approach: Add backend entities and APIs for HOA records, settings, sync history, and file knowledge-base data, then retire `mockData.ts` screen by screen

**Documentation drift:**
- Issue: Some docs no longer match the implementation; for example `docs/decisions/2026-03-12-frontend-backend-integration.md` still says auth and AI are mock, and `docs/AI_PIPELINE_ARCHITECTURE.md` documents Python 3.9 while `backend/Dockerfile` uses Python 3.11
- Why: Runtime code changed faster than written documentation
- Impact: Planning and onboarding can start from stale assumptions
- Fix approach: Audit `docs/` after each integration milestone and explicitly mark superseded decision records

**Large multi-responsibility backend modules:**
- Issue: `backend/app/services/macros_service.py`, `backend/app/generate_budget_pipeline.py`, and `backend/app/ai_implementation/seed/seed_database.py` each own broad, mixed responsibilities
- Why: Fast iteration around workbook behavior and AI experimentation
- Impact: Small changes carry higher regression risk and are harder to isolate or test
- Fix approach: Split parsing, workbook mutation, orchestration, and persistence concerns into smaller modules with stable seams

## Known Bugs

**Sessions reset across backend restarts when `JWT_SECRET_KEY` is unset:**
- Symptoms: Users are logged out after a restart and refresh tokens stop working
- Trigger: Running the backend without a configured `JWT_SECRET_KEY`
- Workaround: Set a persistent secret in `.env`
- Root cause: `backend/app/config.py` generates a random secret at startup when the insecure default is still present

**Budget workflow state disappears on browser refresh:**
- Symptoms: Uploaded workbook, parsed line items, and generated preview state vanish after reload/navigation resets
- Trigger: Refreshing the browser or losing the React component tree while working in `BudgetScreenWrapper`
- Workaround: Re-upload the workbook and regenerate the view
- Root cause: Critical workflow state is kept only in frontend component memory

## Security Considerations

**`/ai/export` is not role-scoped:**
- Risk: Any authenticated user can export all users, properties, suggestion runs, and feedback cases from the SQLite database
- Current mitigation: Route is protected by bearer auth only
- Recommendations: Add role checks or tenant scoping before exposing export data, or remove the endpoint from general user access

**Workbook uploads are trusted too early:**
- Risk: Large or malicious files can be written to disk and parsed without content scanning, size enforcement, or rate limiting
- Current mitigation: Protected routes, temp-file cleanup, and expected Excel-only UI usage
- Recommendations: Enforce upload size/type checks server-side, add antivirus or content validation where appropriate, and rate-limit authenticated upload endpoints

**Environment defaults are easy to misconfigure:**
- Risk: Local-friendly settings such as `COOKIE_SECURE=false` and generated JWT secrets can leak into non-local environments
- Current mitigation: `.env.example` documents the safer production intent
- Recommendations: Fail hard in non-dev environments when secrets or cookie flags are misconfigured

## Performance Bottlenecks

**Workbook-heavy endpoints repeatedly load and save full files:**
- Problem: Endpoints in `backend/app/services/macros_service.py` use `openpyxl` over full workbook files, often reading and writing entire sheets for relatively small mutations
- Measurement: No committed benchmark exists; the code logs durations but no baseline is checked in
- Cause: The implementation favors deterministic file-based transforms over incremental workbook abstractions
- Improvement path: Collapse repeated workbook passes, stream where possible, and cache parsed workbook context during multi-step operations

**AI suggestions run inside a single-worker backend container:**
- Problem: `backend/Dockerfile` runs `uvicorn` with `--workers 1` while AI requests may perform SQLite access, feature engineering, Groq calls, and optional ML inference
- Measurement: `docs/AI_PIPELINE_ARCHITECTURE.md` targets about 5 seconds for 30-50 items, but no production benchmark is committed
- Cause: Simplicity-first deployment plus CPU and network-heavy request work
- Improvement path: Add worker/process scaling, queue long AI jobs, or separate AI execution from the main API process

## Fragile Areas

**Column-position workbook logic:**
- Why fragile: Both backend and frontend assume fixed workbook structure and hard-coded column positions such as `T`, `AG`, `AL`, and `AM`
- Common failures: Template changes or reordered columns silently break parsing, writeback, or read-only detection
- Safe modification: Update `backend/app/services/macros_service.py` and `frontend/src/app/components/BudgetScreen.tsx` together and validate against real sample workbooks
- Test coverage: None in tracked app code

**Auth bridge between refresh cookie and bearer token:**
- Why fragile: Frontend protected calls depend on `AuthContext` keeping a live access token and synchronizing it into `frontend/src/app/api/macros.ts` through `setTokenAccessor()`
- Common failures: Unexpected logout after refresh failure, stale token accessors, or redirects triggered deep inside API helpers
- Safe modification: Change `AuthContext` and API wrappers together and manually verify signup, login, refresh, logout, and protected-route flows
- Test coverage: None in tracked app code

**Startup initialization and seeding:**
- Why fragile: `backend/app/main.py` runs DB init and seed logic in the lifespan hook and only logs a warning on failure
- Common failures: App boots with partial AI functionality or inconsistent seed state while appearing healthy to the rest of the system
- Safe modification: Separate schema init, seeding, and health reporting; make failures explicit where startup correctness matters
- Test coverage: None in tracked app code

## Scaling Limits

**SQLite on local disk:**
- Current capacity: Suitable for local or single-node deployments with modest write concurrency
- Limit: Multi-instance or higher-write production setups will hit file-based coordination and durability limits
- Symptoms at limit: Divergent state across replicas, write contention, and awkward backup/migration workflows
- Scaling path: Move to a server-grade database before horizontal scaling or multi-tenant growth

**Frontend-held workflow state:**
- Current capacity: One active user session in one browser tab works adequately
- Limit: Refreshes, tab handoff, collaboration, and resumable work are not supported
- Symptoms at limit: Repeated uploads, lost unsaved edits, and inability to resume in-progress work
- Scaling path: Persist uploads and draft budget sessions server-side or in durable browser storage with explicit recovery flows

## Dependencies at Risk

**Groq API dependency:**
- Risk: AI suggestion quality and availability depend on an external provider with rate limits and network failure modes
- Impact: Suggestion generation can fail or degrade even when the rest of the product is healthy
- Migration plan: Keep the graceful-degradation path healthy and consider a second provider or internal queue/retry strategy if the feature becomes critical

**CatBoost runtime dependency:**
- Risk: Binary ML dependencies are heavier to maintain and deploy than the rest of the Python stack, especially when re-enabled in slim containers
- Impact: AI training/inference path becomes harder to build, test, and host consistently
- Migration plan: Keep it disabled until there is enough data and deployment maturity, or isolate ML into a separately managed service

## Missing Critical Features

**Tenant and role boundaries beyond basic auth:**
- Problem: The app authenticates users but does not show strong tenant isolation or admin-only feature boundaries
- Current workaround: Implicit trust in whoever can sign in
- Blocks: Safe multi-user rollout and secure export/reporting features
- Implementation complexity: Medium to high because it touches auth, data modeling, and every protected route

**Persistent backend source of truth for HOA and workflow data:**
- Problem: Core product entities like HOA records, settings, and sync history remain mocked on the frontend
- Current workaround: `mockData.ts` and component-local arrays
- Blocks: Real operational use, auditability, and cross-user collaboration
- Implementation complexity: Medium

**Automated verification suite:**
- Problem: There is no backend or frontend automated test coverage
- Current workaround: Manual smoke testing only
- Blocks: Confident refactors in workbook parsing, auth, and AI logic
- Implementation complexity: Medium, but the long-term payoff is high

## Test Coverage Gaps

**Auth and protected-route flows:**
- What's not tested: Signup, login, refresh, logout, cookie behavior, and protected API access
- Risk: Silent auth regressions can lock users out or expose data
- Priority: High
- Difficulty to test: Medium because browser cookie behavior and backend auth need to be exercised together

**Workbook parsing and generation:**
- What's not tested: `parseEnrichedResponse`, percent-change writeback, generated budget preview shaping, and low-level macro endpoints
- Risk: Column-shift regressions or workbook template drift can break the core product path
- Priority: High
- Difficulty to test: Medium to high because realistic workbook fixtures are required

**AI pipeline and feedback retention:**
- What's not tested: Feature engineering, CBR retrieval, Groq fallback behavior, suggestion persistence, and `/ai/feedback`
- Risk: Budget suggestions can degrade or corrupt training data without immediate visibility
- Priority: High
- Difficulty to test: High because the pipeline spans SQLite, seed data, and an external LLM provider

---
*Concerns audit: 2026-03-18*
*Update as issues are fixed or new risks emerge*
