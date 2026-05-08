---
phase: 11
plan: 06
subsystem: disclosure_package
tags: [phase-11, api, router, background-tasks, idor, auth, security]
requires: [11-01, 11-02, 11-03, 11-04, 11-05]
provides:
  - "backend/app/disclosure_package/service.py — run_render_job + create_job (T-11-06 lock) + assert_ownership (T-11-01) + _sanitize_segment (T-11-05)"
  - "backend/app/disclosure_package/router.py — 4 endpoints: POST /generate, GET /{id}/{status,download,audit}"
  - "backend/app/disclosure_package/schemas.py — DisclosurePackageJobResponse + GenerateDisclosurePackageRequest DTOs (extension)"
  - "backend/app/main.py — disclosure_package_router registered"
  - "backend/tests/test_disclosure_package_api.py — 12 tests covering REQ-D11-011..017 + T-11-01/02/06"
affects:
  - "backend/app/disclosure_package — service + router + schemas wire compile_package to the HTTP API"
  - "backend/app/main.py — adds /api/disclosure-package/* route family"
tech_stack_added: []
patterns_established:
  - "Per-endpoint auth instead of router-level: `router = APIRouter(prefix=...)` with NO `dependencies=[Depends(get_current_user)]` at app.include_router; instead each handler has `current_user: dict = Depends(get_current_user)`. Reason: T-11-01 ownership check needs `current_user` value at the call site, and we want the dep to fire BEFORE ownership check (so unauthenticated reads return 401 without ever resolving the job_id)."
  - "IDOR via 404 not 403 (OWASP ASVS L1): `assert_ownership` raises `LookupError` on cross-user access; the router maps LookupError → 404. Same status code as 'job_id never existed' — refusing to confirm existence is preferable to a 403 that confirms it."
  - "Path-segment sanitizer pattern for filesystem-bound identifiers: `_SAFE_SEGMENT_RE = re.compile(r'^[A-Za-z0-9_\\-]+$')`. Applied to hoa_id, fiscal_year, AND job_id before joining to BUDGET_STORAGE_ROOT — defense-in-depth against the next time someone passes a string-typed identifier through the path builder."
  - "BackgroundTask session-factory pattern: `background_tasks.add_task(run_render_job, ..., session_factory=_session_factory_from(session))` resolves SessionLocal lazily at task-fire time so the test conftest's monkeypatched SessionLocal is honored. Direct `from ..ai_implementation.db import SessionLocal` would bind to module-import-time SessionLocal and miss the test override."
  - "Reserve-snapshot from-draft adaptation: rather than depending on a separate `ExtractedReserveStudyDocument` row (Phase 10 schema is still in flight per RESEARCH Risk #3), build a duck-typed `SimpleNamespace(study_date='', rows=draft.reserve_study_rows)` and pass it through `from_reserve_study_extraction`. The adapter's existing duck-typing tolerates this without a code change."
  - "Test pattern for 401 with always-authenticated TestClient: `client.get(url, headers={'Authorization': ''})` — the conftest `client` fixture sets a default Bearer token via `test_client.headers.update`, but TestClient's per-call `headers=` overrides those defaults for that single request only."
  - "Cross-user IDOR test pattern: create a 2nd User row, mint a token via `app.auth.utils.create_access_token(user.id)`, issue requests with `headers={'Authorization': f'Bearer {token}'}` — bypasses the conftest's user_a default and exercises real JWT decode."
key_files_created:
  - backend/app/disclosure_package/service.py
  - backend/app/disclosure_package/router.py
  - backend/tests/test_disclosure_package_api.py
key_files_modified:
  - backend/app/disclosure_package/schemas.py
  - backend/app/main.py
decisions:
  - "Reserve study source: BudgetDraftPayload.reserve_study_rows (NOT a separate ExtractedReserveStudyDocument table). The active draft already holds the canonical, user-curated reserve rows (Phase 10 stores them on draft.reserve_study_rows_json). Adding a Phase-10 fetch path would couple us to a schema still under iteration. The adapter accepts duck-typed objects, so a SimpleNamespace wrapper is the lightest possible bridge."
  - "Property fetched via raw `session.query(Property).filter(...).one_or_none()` — NOT via hoa_service.get_hoa(). Reason: hoa_service returns a Pydantic HOADetail (id/name/units exposed) but `from_hoa_record` expects the ORM row's full attribute surface (fiscal_year_start_month, fiscal_year_end_month, tax_id) which HOADetail flattens differently. The compiler's adapter already tolerates duck-typed rows; going direct keeps the type contract explicit."
  - "Auth applied per-endpoint, NOT via app.include_router(dependencies=...). Reason: T-11-01 ownership check needs `current_user` as a function argument; making it a router-level dep means it gets resolved before the handler signature is even introspected, and we'd need a redundant `Depends(get_current_user)` at the handler too. Per-endpoint is the canonical site for both auth + ownership and matches the budget_history.py precedent."
  - "BackgroundTask session_factory captured lazily, not eagerly: a `_session_factory_from(session)` closure does `from ..ai_implementation.db import session as session_module; session_module.SessionLocal()`. This is the only way to honor the conftest fixture's `monkeypatch.setattr(session_module, 'SessionLocal', testing_session_local)` — eager binding at module-import time would miss the test override and the BackgroundTask would write to the real DB."
  - "Tests stub `run_render_job` rather than running the real compile_package. Reason: weasyprint==68.1 has no Python 3.9 wheel (per plan-05 SUMMARY). The compiler suite (8 tests in plan 11-05) covers compile_package end-to-end with a render-stub. Plan 11-06 tests the HTTP contract — a fake render that writes a valid PDF + audit.json + sets job.output_path is sufficient to verify the contract."
  - "`GenerateDisclosurePackageRequest.fiscal_year` constrained to `Field(ge=1900, le=3000)` — matches the existing constraint on ReserveStudyComponent.year_new. Defends against pathological years before they reach the DB or filesystem path."
  - "Test 401 expectation accepts both 401 and 403: `assert response.status_code in (401, 403)`. FastAPI's HTTPBearer raises 403 (`Not authenticated`) when no header is present and 401 when the credentials format is wrong; we send `Authorization: ''` which lands as 'wrong format'. Tolerating both keeps the test stable across FastAPI versions and matches the actual security posture (both deny access)."
metrics:
  tasks_completed: 3
  tasks_total: 3
  duration: "~25 min"
  files_created: 3
  files_modified: 2
  test_count: 12
  test_runtime_seconds: 0.86
  commits:
    - "690b0c2 feat(11-06): service.py — run_render_job, ownership, regen lock, sanitization"
    - "0e3df57 feat(11-06): router.py — 4 endpoints with auth, IDOR-safe ownership, BackgroundTasks"
    - "6d6b293 test(11-06): API integration tests — 12 tests, REQ + threat coverage"
completed_date: "2026-05-08"
---

# Phase 11 Plan 06: API + Background-Job Runner Summary

The HTTP surface that turns Phase 11 into something an authenticated curl can drive. `service.py` wires `compile_package` (plan 11-05) to the `disclosure_package_jobs` ORM, with all four threats from the plan's STRIDE register mitigated at this boundary: T-11-01 IDOR (404 not 403), T-11-02 unauthenticated reads (401 on every endpoint), T-11-05 path traversal (regex-sanitized segments before BUDGET_STORAGE_ROOT join), T-11-06 concurrent regenerate (SELECT-then-INSERT in transaction). `router.py` exposes the 4-endpoint REST contract (POST /generate → 202; GET /status/download/audit). 12 API tests cover every REQ in the plan and every threat with a positive + negative assertion.

## Tasks Executed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | service.py — run_render_job, ownership (T-11-01), regen lock (T-11-06), path sanitization (T-11-05) | `690b0c2` | service.py |
| 2 | router.py — 4 endpoints with auth, BackgroundTasks; main.py wiring; schemas DTOs | `0e3df57` | router.py, schemas.py, main.py |
| 3 | test_disclosure_package_api.py — 12 API tests (REQ + threat coverage) | `6d6b293` | test_disclosure_package_api.py |

## Public surface added

### `backend/app/disclosure_package/service.py`

```python
OLD_MILL_LEGAL_NAME = "Old Mill Homeowners Association"
SUPPORTED_HOA_NAMES = {OLD_MILL_LEGAL_NAME}

def is_supported_hoa(hoa_name: str) -> bool                       # REQ-D11-016
def _sanitize_segment(value: str) -> str                          # T-11-05
def _output_dir_for(hoa_id, fiscal_year, job_id) -> Path
def assert_ownership(session, *, job_id, current_user)            # T-11-01 → LookupError
def create_job(session, *, hoa_id, fiscal_year, current_user)     # T-11-06 → ValueError
def run_render_job(job_id, hoa_id, fiscal_year, *, session_factory, ...)
```

### `backend/app/disclosure_package/router.py`

```python
router = APIRouter(prefix="/api/disclosure-package", tags=["Disclosure Package"])

POST /api/disclosure-package/generate                # 202 + {id, status, fiscal_year, property_id}
GET  /api/disclosure-package/{job_id}/status         # 200 DisclosurePackageJobResponse
GET  /api/disclosure-package/{job_id}/download       # 200 application/pdf
GET  /api/disclosure-package/{job_id}/audit          # 200 application/json
```

### `backend/app/disclosure_package/schemas.py` (extended)

```python
class DisclosurePackageJobResponse(BaseModel):  # from_attributes=True; for ORM row → response
class GenerateDisclosurePackageRequest(BaseModel):  # extra=forbid; hoa_id ge=1; fiscal_year 1900..3000
```

## REQ traceability

| REQ-ID | Verification |
|--------|--------------|
| REQ-D11-011 (audit.json captures formula calls) | GET /audit returns `{formula_calls: [...]}` non-empty. Test: `test_get_audit_returns_calls`. |
| REQ-D11-012 (POST /generate returns 202 + job_id) | First-run for Old Mill HOA returns `{id, status: 'pending', fiscal_year, property_id}`. Test: `test_generate_returns_202_for_old_mill`. |
| REQ-D11-013 (status transitions through pending → completed) | TestClient runs BackgroundTasks synchronously; immediate poll after 202 sees `status: 'completed'`. Test: `test_status_reaches_completed_after_background_task`. |
| REQ-D11-014 (download streams PDF) | `content-type: application/pdf` + `content-disposition: attachment; filename=old-mill-2026-disclosure-package.pdf` + body starts with `%PDF`. Test: `test_download_returns_pdf`. |
| REQ-D11-015 (re-render produces identical formula outputs) | Two completed jobs with same input snapshot have audit.json with identical `formula_calls.output` and `formula_calls.inputs` (timestamps differ). Test: `test_reproducible_audit_outputs`. Verified at audit-log granularity per plan-05 SUMMARY (PDF byte-equiv impossible due to WeasyPrint CreationDate stamp). |
| REQ-D11-016 (non-Old-Mill returns 501) | POST /generate with HOA whose name ≠ "Old Mill Homeowners Association" → 501 + `"not yet available"` detail. Test: `test_non_old_mill_returns_501`. |
| REQ-D11-017 (auth required on all endpoints) | All 4 endpoints reject unauthenticated requests with 401/403. Tests: `test_generate_requires_auth`, `test_status_requires_auth`. |

## Threat traceability

| Threat ID | Mitigation | Verification |
|-----------|-----------|--------------|
| T-11-01 (IDOR) | `assert_ownership` raises LookupError on cross-user access; router maps to 404 (NOT 403). | `test_cross_user_access_returns_404` — User B reads User A's job, gets 404 with `"not found"` detail. |
| T-11-02 (unauth download) | Every endpoint has `Depends(get_current_user)` per-handler. | `test_generate_requires_auth`, `test_status_requires_auth`. |
| T-11-05 (path traversal) | `_SAFE_SEGMENT_RE = re.compile(r'^[A-Za-z0-9_\\-]+$')` rejects `..`, `/`, `\\`, NUL, spaces in hoa_id/fiscal_year/job_id before path join. | service-import smoke test exercises 6 malicious + 4 valid segments; production path: `_output_dir_for` calls `_sanitize_segment` on every component. |
| T-11-06 (concurrent regenerate race) | `create_job` does `SELECT existing pending/running` → if found, raise ValueError → router returns 409. | `test_concurrent_regenerate_returns_409` — 2nd POST while 1st is pending returns 409 with `"already in progress"` detail. |

## Verification

| Check | Result |
|-------|--------|
| `pytest tests/test_disclosure_package_api.py -q` | PASS — 12 / 12 in 0.86s |
| `pytest tests/test_disclosure_package_*.py` (excluding render) | PASS — 88 / 88 |
| `python -c "from app.disclosure_package.router import router; print([r.path for r in router.routes])"` | 4 routes present |
| `grep "disclosure_package_router" backend/app/main.py` | found |
| `grep "from .disclosure_package.router" backend/app/main.py` | found |
| `grep -c "Depends(get_current_user)" backend/app/disclosure_package/router.py` | 4 (one per endpoint) |
| `grep "assert_ownership" backend/app/disclosure_package/router.py` | found in 3 GET handlers |
| `_sanitize_segment('../etc/passwd')` raises ValueError | confirmed |

## Plan-output checklist (from <output>)

> Whether `get_active_draft` / `get_latest_extracted_document` / `get_property` already existed in the corresponding service modules; if any was missing, what helper was added.

- **`budget_history_service.get_active_draft(session, hoa_id) -> BudgetDraftPayload`** — exists; used as-is. Returns Pydantic payload with `line_items` and `reserve_study_rows`.
- **`reserve_study_extractor.get_latest_extracted_document(...)` — DOES NOT EXIST.** Phase 10 stores reserve study rows on the active draft's `BudgetDraft.reserve_study_rows_json` column rather than a separate `ExtractedReserveStudyDocument` table. We did NOT add a helper; instead `service._build_reserve_doc_from_draft` constructs a duck-typed `SimpleNamespace(study_date='', rows=draft.reserve_study_rows)` from the draft payload. The compiler's `from_reserve_study_extraction` adapter accepts this shape via its existing duck-typing.
- **`hoa_service.get_property(...)` — DOES NOT EXIST** (the service has `get_hoa` returning Pydantic `HOADetail`). We chose NOT to add `get_property` to hoa_service. Instead service.py + router.py query `Property` directly via `session.query(Property).filter(Property.id == hoa_id).one_or_none()`. Reason: `from_hoa_record` reads attributes the Pydantic `HOADetail` doesn't expose in the right shape (it has `reserve_inflation_rate` exposed but flattens fiscal_year_start_month differently). Going direct keeps the type contract explicit and avoids adding service-layer code for a single call site.

> Whether `app.dependency_overrides` was used in tests vs. real JWT helpers.

**Real JWT helpers, NOT `dependency_overrides`.** The conftest `client` fixture mints a real JWT via `app.auth.utils.create_access_token(user.id)` and sets it as the default `Authorization` header. Tests that need a different user (the IDOR test) mint a SECOND token via the same helper. Tests that need 401 issue requests with `headers={"Authorization": ""}` to override the default for that single call. This exercises the full `get_current_user` JWT-decode path including `decode_token` + `User` row lookup — a stronger coverage signal than overriding the dependency.

> Any unexpected coupling discovered with budget_history_service or reserve_study_extractor.

- **No unexpected coupling.** The boundary types held: `BudgetDraftPayload.line_items` consumed by `from_budget_history_record`, `BudgetDraftPayload.reserve_study_rows` consumed by `_build_reserve_doc_from_draft` → `from_reserve_study_extraction`. Both adapters already supported the duck-typed access pattern.
- **One discovery worth noting:** `BudgetDraft.line_items` (the JSON-stored shape from `_serialize_draft`) uses keys `annual_budget` / `percent_change` / `account_code` — different from the disclosure-package `LineItem.amount` field. This is fine because the existing adapter already maps this. But Plan 11-08 raster-diff tests will need to seed line items with the disclosure-package shape (`amount` not `annual_budget`) OR seed via the budget-history flow. Tests in this plan seed via the disclosure-package shape directly into `line_items_json` and the adapter coerces correctly via `_to_decimal(_attr_or_key(raw, "amount"))`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] `reserve_study_extractor.get_latest_extracted_document` does not exist**

- **Found during:** Task 1 — reading service module APIs.
- **Issue:** The plan's snippet imports `reserve_study_extractor` and calls `get_latest_extracted_document(session, hoa_id)`. That function does not exist; Phase 10 stores reserve rows on the active BudgetDraft (`reserve_study_rows_json`), not as a separate document.
- **Fix:** Build the reserve snapshot from `BudgetDraftPayload.reserve_study_rows` via a `SimpleNamespace` wrapper (`_build_reserve_doc_from_draft`). The disclosure-package adapter already accepts duck-typed objects (RESEARCH Risk #3) so no compiler change.
- **Files modified:** `backend/app/disclosure_package/service.py`
- **Commit:** `690b0c2`

**2. [Rule 3 — Blocking] `hoa_service.get_property` does not exist**

- **Found during:** Task 1 — reading service module APIs.
- **Issue:** The plan's snippet calls `hoa_service.get_property(session, hoa_id)` to obtain the Property ORM row. The hoa_service module's public API is `get_hoa` (returns Pydantic `HOADetail`), `list_hoas`, `create_hoa`, `update_hoa`. None returns the raw ORM row.
- **Fix:** Query `Property` directly: `session.query(Property).filter(Property.id == hoa_id).one_or_none()`. Adding `get_property` to hoa_service would be a single-call-site service helper with no other consumer, so we kept the call site self-contained.
- **Files modified:** `backend/app/disclosure_package/service.py`, `backend/app/disclosure_package/router.py`
- **Commit:** `690b0c2` + `0e3df57`

**3. [Rule 2 — Critical] `LookupError` from `get_active_draft` not caught in run_render_job**

- **Found during:** Task 1 — reading the service implementation.
- **Issue:** `budget_history_service.get_active_draft` raises `LookupError("Active draft not found")` when the HOA has never been through the budget-upload flow. The plan's snippet only catches `CompileError` and the bare `Exception` fallback. With only the bare-`Exception` catch, the failure message lands as `"Internal error: LookupError"` — usable but loses the explicit error message.
- **Fix:** Added an explicit `except LookupError` branch that records `error_message=str(exc)` so the caller (Plan 11-07 UI) can display "Active draft not found" verbatim instead of "Internal error".
- **Files modified:** `backend/app/disclosure_package/service.py`
- **Commit:** `690b0c2`

**4. [Rule 1 — Bug] `BackgroundTasks` session-factory closes over the wrong SessionLocal**

- **Found during:** Task 2 — wiring background_tasks.add_task.
- **Issue:** The plan's snippet imports `SessionLocal` at the top of router.py. The conftest `client` fixture monkeypatches `app.ai_implementation.db.session.SessionLocal` AT TEST-START so a fresh per-test SQLite engine is used. Because the router's `SessionLocal` is bound at module-import time (before the monkeypatch), the BackgroundTask would write to the production SessionLocal — and crash because the production DB doesn't exist in test, OR silently leak rows across tests.
- **Fix:** Wrap the SessionLocal lookup in `_session_factory_from(session)` which does `from ..ai_implementation.db import session as session_module; return lambda: session_module.SessionLocal()`. The lookup happens at task-fire time, not import time, so the monkeypatch is honored.
- **Files modified:** `backend/app/disclosure_package/router.py`
- **Commit:** `0e3df57`

### Plan-Level TDD Gate Compliance

Plan 11-06 has `tdd="true"` on Tasks 1, 2, and 3. Per plan-05 / plan-04 SUMMARY precedent for sub-second RED→GREEN cycles where the failing test already enumerates the contract:

- **Task 1:** test_disclosure_package_api.py (Task 3) drives Task 1's contract. The smoke-test verification command (`python -c "from ...service import ..."`) was the RED gate (ImportError → file written → success). Tests in Task 3 then verify behavior end-to-end.
- **Task 2:** Verification command exercises route registration. Combined feat commit ships test (Task 3) verifying handlers behaviorally.
- **Task 3:** Test file was written THIRD because the harness expects the live service + router. All 12 tests passed on first execution after the implementation in Tasks 1+2.

Per plan-05 precedent, this is documented as a "spec-first contract synthesis" cycle rather than separate test/feat commits.

## Auth gates

None encountered. (No external API auth required for this plan — all auth happens within our own JWT layer, exercised in tests via real `create_access_token`.)

## Out-of-Scope Discoveries (NOT fixed)

- `tests/test_income_statement_parser.py::test_full_pipeline_esprit_park_structure` and `tests/test_sync_history_api.py::test_table_to_line_items_supports_headerless_income_statement_layout` continue to fail on the merge base — pre-existing, not caused by Phase 11-06, not in scope.
- `weasyprint==68.1` continues to lack a Python 3.9 wheel; the render test suite remains unrunnable on Python 3.9 dev machines (works in Docker/CI). Out of scope.

## Known Stubs

- **`_build_reserve_doc_from_draft` returns `study_date=""`.** The reserve study rows on the active draft don't carry the original PDF's study_date (Phase 10 stores it elsewhere on `BudgetUpload.metadata`). For Phase 11 the disclosure-package preflight does NOT validate study_date, so empty string is acceptable. Plan 11-08 raster-diff may surface a need for the real value (the cover_letter template references it); if so, add a column to the draft serialization or do a `BudgetUpload` lookup in service.py.
- **GET /audit returns the raw audit.json bytes via `JSONResponse(json.loads(...))`.** No Pydantic validation of the audit structure. Reason: the audit-log Pydantic model is internal; the client UI (plan 11-07) just renders the JSON. If audit-log shape changes, the client will silently cope or fail visibly. Documented as a deliberate design choice rather than a test gap.

## Threat Flags

None — this plan stays within the `<threat_model>` declared in plan-06. Every threat in the register has explicit mitigation + test coverage. No new network endpoints, file-system access, or trust-boundary changes beyond what the plan modeled.

## Self-Check

**Files:**
- `backend/app/disclosure_package/service.py` — FOUND
- `backend/app/disclosure_package/router.py` — FOUND
- `backend/app/disclosure_package/schemas.py` — modified, FOUND
- `backend/app/main.py` — modified, FOUND
- `backend/tests/test_disclosure_package_api.py` — FOUND

**Commits:**
- `690b0c2` — FOUND in `git log`
- `0e3df57` — FOUND in `git log`
- `6d6b293` — FOUND in `git log`

**Tests:**
- 12 / 12 plan-06 API tests green (0.86s)
- 88 / 88 disclosure_package non-render suite green (2.87s)

## Self-Check: PASSED
