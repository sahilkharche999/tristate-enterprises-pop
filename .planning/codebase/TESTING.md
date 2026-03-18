# Testing Patterns

**Analysis Date:** 2026-03-18

## Test Framework

**Runner:**
- No first-party backend or frontend test runner is configured in tracked app code
- `frontend/package.json` has no `test` script
- No pytest config, Vitest config, Jest config, Playwright config, or Cypress config was found

**Assertion Library:**
- None established in repo source

**Run Commands:**
```bash
cd backend && uvicorn app.main:app --reload     # Manual backend smoke run
cd frontend && pnpm run build                   # Frontend build verification
docker compose up -d --env-file .env            # Full-stack manual integration run
```

## Test File Organization

**Location:**
- No first-party `test_*.py` or `*_test.py` files exist under tracked backend app code
- No first-party `*.test.*` or `*.spec.*` files exist under tracked frontend app code
- The only test-like hits during discovery came from vendored environments such as `backend/.venv/`, not from the project itself

**Naming:**
- No established naming convention exists yet

**Structure:**
```text
backend/     # No tracked backend tests
frontend/    # No tracked frontend tests
```

## Test Structure

**Suite Organization:**
- Not established
- Current verification is manual and flow-based rather than suite-based

**Patterns:**
- Backend behavior is typically checked by running the FastAPI app and exercising endpoints through the browser or docs UI
- Frontend behavior is checked by interacting with the SPA and by ensuring the Vite build succeeds
- AI pipeline validation currently leans on seed data and runtime behavior rather than repeatable automated assertions

## Mocking

**Framework:**
- No mocking framework is configured

**Patterns:**
- Frontend relies on real in-app mock data in `frontend/src/app/data/mockData.ts` for many screens instead of test-only mocks
- Backend seed inputs under `backend/app/ai_implementation/seed/data/` support manual scenario setup, not isolated unit mocking

**What to Mock Today:**
- Not standardized

**What NOT to Mock Today:**
- Not standardized

## Fixtures and Factories

**Test Data:**
- `backend/app/ai_implementation/seed/data/ai_agent_casebase.json`
- `backend/app/ai_implementation/seed/data/ai_agent_feedback.jsonl`
- `backend/app/ai_implementation/seed/data/*.xlsx`
- `frontend/src/app/data/mockData.ts`

**Location:**
- These files function as development fixtures and demo data
- They are not wired into an automated test harness

## Coverage

**Requirements:**
- No coverage target is configured
- No CI gate enforces test or coverage thresholds

**Configuration:**
- No coverage tooling config exists in the repo

**View Coverage:**
```bash
# Not available today
```

## Test Types

**Unit Tests:**
- None implemented

**Integration Tests:**
- None implemented

**E2E Tests:**
- None implemented

**Manual Validation Flows Used Today:**
- Sign up, log in, and refresh session through the SPA
- Upload an income statement, enrich it, edit percent changes, and generate a budget
- Fetch AI suggestions and submit feedback
- Run local containers with `docker compose`

## Common Patterns

**Current Reality:**
- Manual smoke testing is the only established verification pattern
- Internal planning docs under `docs/superpowers/plans/` reference pytest-based AI tests, but the referenced `ai_implementation/tests/` directory is not present in this repo
- Any new automated test effort will need to establish both tooling and file placement conventions from scratch

**Practical Guidance for Future Work:**
- Decide on a backend runner before adding tests piecemeal
- Decide whether frontend tests should live beside components or under a dedicated test tree
- Add scripts to `package.json` and a documented backend command the same day the first tests land

---
*Testing analysis: 2026-03-18*
*Update after the first real automated test suite is added*
