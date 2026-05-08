---
phase: 11
plan: 01
subsystem: disclosure_package
tags: [phase-11, disclosure-package, schema, infrastructure, blocking, foundation]
requires: []
provides:
  - "weasyprint==68.1, Jinja2==3.1.6, pypdf==6.10.2 pinned in backend/requirements.txt"
  - "qpdf binary + WeasyPrint native deps + Liberation/DejaVu/Noto fonts in backend/Dockerfile"
  - "disclosure_package_jobs table (TEXT uuid4 PK, property_id FK ON DELETE CASCADE, status CHECK enum)"
  - "DisclosurePackageJob ORM model with lazy='raise' Property relationship"
  - "Old Mill Homeowners Association seed row (hoa_code='10', units=279)"
  - "backend/app/disclosure_package/ package marker for Phase 11 subsystem"
  - "pytest fixtures: disclosure_storage_root, golden_old_mill_pdf, qpdf_required"
affects:
  - backend/requirements.txt
  - backend/Dockerfile
  - backend/app/ai_implementation/schema.sql
  - backend/app/ai_implementation/db/models.py
  - backend/app/ai_implementation/db/__init__.py
  - backend/app/ai_implementation/seed/seed_database.py
  - backend/tests/conftest.py
tech_stack_added:
  - weasyprint==68.1
  - Jinja2==3.1.6
  - pypdf==6.10.2
  - qpdf (system binary, apt-get)
  - fonts-liberation, fonts-dejavu, fonts-noto-core (apt-get)
patterns_established:
  - "TEXT uuid4 primary keys for externally-exposed job ids (NOT autoincrement) — anti-leak"
  - "Module-level state constants mirror schema CHECK enums (DISCLOSURE_JOB_*, DISCLOSURE_STAGE_*)"
  - "tmp_path-rooted BUDGET_STORAGE_ROOT in test fixtures (RESEARCH Pitfall 7)"
key_files_created:
  - backend/app/disclosure_package/__init__.py
key_files_modified:
  - backend/requirements.txt
  - backend/Dockerfile
  - backend/app/ai_implementation/schema.sql
  - backend/app/ai_implementation/db/models.py
  - backend/app/ai_implementation/db/__init__.py
  - backend/app/ai_implementation/seed/seed_database.py
  - backend/tests/conftest.py
decisions:
  - "Old Mill seed was ABSENT from PORTFOLIO_SEED — added as hoa_code='10', units=279, FY Jan-Dec, portfolio_year=2026, Mountain View. tax_id is placeholder ('00-0000000') pending Bob's confirmation per CONTEXT § 'Inputs to Hardcode for Old Mill'."
  - "Railway volume DDL strategy: rely on existing init_db() startup invocation, which executes schema.sql with CREATE TABLE IF NOT EXISTS. Next backend deploy auto-creates the new table. No manual railway-shell DDL command required. Idempotency was confirmed by local test (init_db on a fresh sqlite file produces the exact CREATE TABLE definition + idx_disclosure_jobs_property index)."
  - "Accepted T-11-06 regenerate-race risk for MVP: no UNIQUE(property_id, fiscal_year) WHERE status='running' constraint. Plan 11-06 will use SELECT-then-INSERT in a transaction; full enforcement deferred per Phase 11 single-user assumption."
  - "Job ids are TEXT uuid4 strings (NOT autoincrement integers) so URL-exposed job_id never leaks creation rate — diverges from BudgetVersion analog per PATTERNS § 'Diverge from analog'."
metrics:
  tasks_completed: 4
  tasks_total: 4
  duration: "~10 min"
  files_created: 1
  files_modified: 6
  commits:
    - "8e1413d chore(11-01): pin Phase 11 rendering deps + add Docker native libs"
    - "776673e feat(11-01): add disclosure_package_jobs table + DisclosurePackageJob ORM"
    - "db7dd52 feat(11-01): seed Old Mill HOA + create disclosure_package package + test fixtures"
completed_date: "2026-05-08"
---

# Phase 11 Plan 01: Stack & Schema Foundation Summary

WeasyPrint 68.1 + Jinja2 3.1.6 + pypdf 6.10.2 pinned, Docker image now ships qpdf binary and bundled Liberation/DejaVu/Noto fonts, `disclosure_package_jobs` table + `DisclosurePackageJob` ORM model added with TEXT uuid4 primary key and `lazy="raise"` Property relationship, Old Mill Homeowners Association seeded into `PORTFOLIO_SEED`, empty `backend/app/disclosure_package/` package marker created, and three pytest fixtures (`disclosure_storage_root`, `golden_old_mill_pdf`, `qpdf_required`) wired into `tests/conftest.py`. The next plan (11-02 calc engine) can begin without further infrastructure work.

## Tasks Executed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | Add Phase 11 dependencies to requirements.txt and Dockerfile | `8e1413d` | backend/requirements.txt, backend/Dockerfile |
| 2 | Add disclosure_package_jobs table + DisclosurePackageJob ORM | `776673e` | backend/app/ai_implementation/schema.sql, db/models.py, db/__init__.py |
| 3 | [BLOCKING] Schema migration to dev SQLite + Railway volume strategy | (no code commit — checkpoint) | local SQLite verified; Railway plan documented |
| 4 | Seed Old Mill HOA + create disclosure_package package + test fixtures | `db7dd52` | seed_database.py, app/disclosure_package/__init__.py, tests/conftest.py |

## Key Decisions

### Old Mill Seed Status — ABSENT, Added in Task 4

`PORTFOLIO_SEED` in `backend/app/ai_implementation/seed/seed_database.py` did NOT contain Old Mill before this plan (`grep -ic "old mill"` returned 0). Task 4 appended a new entry as `hoa_code='10'` (next free slot after the existing 1–9), `name="Old Mill Homeowners Association"`, `units=279`, `fiscal_year_start_month=1`, `fiscal_year_end_month=12`, `portfolio_year=2026`, `city="Mountain View"`, `tax_id="00-0000000"` (placeholder pending Bob's confirmation per CONTEXT § "Inputs to Hardcode for Old Mill"). The existing `sync_portfolio_properties()` upsert is idempotent (FK key on hoa_code + name fallback) so re-running the seed against an existing database is safe.

### Railway Volume Schema Apply Strategy — Auto-Apply via init_db()

The plan's Task 3 BLOCKING checkpoint asked for confirmation of the production DDL strategy. Investigation of `backend/app/ai_implementation/database.py:154-167` confirmed that `init_db()` runs `schema.sql` via `executescript()` against the live SQLite file on every backend startup, and that the new DDL uses `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`, so it is fully idempotent. Therefore: **no manual Railway-shell DDL command is required**. The next backend deploy that runs through Railway's normal `railway up --path-as-root backend …` flow will create the table automatically.

Local DDL apply was verified by:

```bash
DB_PATH=<tmpdir>/test.db python -c "from app.ai_implementation.database import init_db; init_db()"
sqlite3 <tmpdir>/test.db ".schema disclosure_package_jobs"
# → emits the full CREATE TABLE definition including the property_id FK and CHECK constraint
sqlite3 <tmpdir>/test.db ".indexes disclosure_package_jobs"
# → emits idx_disclosure_jobs_property
```

Production volume schema-divergence risk (RESEARCH risk #4 / Task 3 acceptance bullet 3): no prior `disclosure_package_jobs` table exists on the volume — this is the first plan that introduces it, so there is no risk of an `IF NOT EXISTS` silently skipping a divergent earlier definition. Future plans (11-02+) that ALTER this table will need the brownfield-migration `ensure_*_columns()` helper pattern from `database.py:131-151`.

### Auto-Mode Posture for the BLOCKING Checkpoint

Per parallel-executor + auto-mode rules ("Auto mode is not a license to destroy. Anything that deletes data or modifies shared or production systems still needs explicit user confirmation"), Task 3's `checkpoint:human-action` did NOT trigger any production-side action. The strategy is documented and the next deploy of the backend service from the user's machine via the Railway CLI will apply the DDL through the existing idempotent startup hook. If the user prefers an explicit pre-deploy DDL apply, the fallback is documented in the plan: `railway run --service tristate-product-delivery -- sqlite3 /app/app/ai_implementation/data/<dbfile>.db < /app/app/ai_implementation/schema.sql`.

### Job ID Shape — TEXT uuid4 (Diverges from BudgetVersion Analog)

`DisclosurePackageJob.id` is `Column(Text, primary_key=True)`, NOT `Integer, autoincrement=True` as in the analog `BudgetVersion`. Job ids are exposed in URLs (`GET /disclosure-package/{job_id}/status`) and a sequential surrogate would leak job-creation rate. The router in plan 11-06 will populate the field with `str(uuid.uuid4())`. PATTERNS § "Diverge from analog where" calls this out explicitly.

### Threat Mitigations Wired In

- **T-11-01 (IDOR):** `property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE` — gives the router a stable anchor for the ownership check that plan 11-06 enforces via `Depends(get_current_user)`. Cascade delete prevents orphans when a property is removed.
- **T-11-03 (font-fetch SSRF):** Dockerfile now installs `fonts-liberation`, `fonts-dejavu`, `fonts-noto-core` so WeasyPrint resolves all referenced fonts locally. Plan 11-04 will additionally wire an explicit `url_fetcher` that denies network access at render time.
- **T-11-05 (path traversal):** `disclosure_storage_root` test fixture sandboxes test runs to `tmp_path`. Production filename builder in plan 11-06 will sanitize `hoa_id` and `fiscal_year` before joining to `BUDGET_STORAGE_ROOT`.
- **T-11-06 (regenerate race):** Accepted for MVP. SELECT-then-INSERT in a transaction at the service layer (plan 11-06) is the documented mitigation; full unique-constraint enforcement deferred per Phase 11 single-user assumption.

## Verification

| Check | Result |
|-------|--------|
| `grep -q "^weasyprint==68.1$" backend/requirements.txt` | PASS |
| `grep -q "^Jinja2==3.1.6$" backend/requirements.txt` | PASS |
| `grep -q "^pypdf==6.10.2$" backend/requirements.txt` | PASS |
| `grep -q "libpango-1.0-0" backend/Dockerfile` | PASS |
| `grep -q "qpdf" backend/Dockerfile` | PASS |
| `grep -q "fonts-liberation" backend/Dockerfile` | PASS |
| `grep -q "CREATE TABLE IF NOT EXISTS disclosure_package_jobs" backend/app/ai_implementation/schema.sql` | PASS |
| `grep -q "CREATE INDEX IF NOT EXISTS idx_disclosure_jobs_property" backend/app/ai_implementation/schema.sql` | PASS |
| `python -c "from app.ai_implementation.db.models import DisclosurePackageJob, DISCLOSURE_JOB_PENDING; assert DISCLOSURE_JOB_PENDING == 'pending'; assert DisclosurePackageJob.__tablename__ == 'disclosure_package_jobs'"` | PASS |
| `python -c "import app.disclosure_package"` | PASS |
| `grep -qi "old mill" backend/app/ai_implementation/seed/seed_database.py` | PASS |
| `grep -q "disclosure_storage_root" backend/tests/conftest.py` | PASS |
| `grep -q "golden_old_mill_pdf" backend/tests/conftest.py` | PASS |
| `grep -q "qpdf_required" backend/tests/conftest.py` | PASS |
| `python -m pytest backend/tests/conftest.py --collect-only` | PASS (no collection errors) |
| Local `init_db()` against fresh sqlite produces full CREATE TABLE + index | PASS |

## Deviations from Plan

None — plan executed exactly as written.

The BLOCKING Task 3 was resolved without manual production action because investigation showed the existing `init_db()` startup hook already applies `schema.sql` idempotently. The Railway-side decision (auto-apply on next deploy vs. explicit pre-deploy DDL) is documented for the user; this plan did NOT touch the production volume per the parallel-executor + auto-mode no-destructive-actions guard.

## Deferred Issues / Out-of-Scope Discoveries

- **`tax_id` for Old Mill is a placeholder** (`"00-0000000"`). Bob has not yet provided the real federal tax id; this is tracked in CONTEXT § "Inputs to Hardcode for Old Mill" alongside the unconfirmed reserve cash balance, bank/CD balance, and income tax provision. A follow-up admin-input form (Phase 12 or 13) will collect these.
- **`requirements.txt` install verification not run.** `pip install -r requirements.txt` was NOT executed in this plan — the local Python 3.9 environment cannot install `weasyprint==68.1` without the system libpango/harfbuzz layer the Dockerfile adds. Verification deferred to the first Docker build by plan 11-02 (or to the user's next `railway up` of the backend service). The pin-format itself is syntactically valid (verified by `grep`).
- **Frontend `dist/` artifacts** (untracked from prior phase) are NOT touched by this plan and remain untracked.

## Self-Check: PASSED

- File `backend/app/disclosure_package/__init__.py` — FOUND
- File `backend/requirements.txt` — FOUND, contains all three pins
- File `backend/Dockerfile` — FOUND, contains libpango / qpdf / fonts-liberation
- File `backend/app/ai_implementation/schema.sql` — FOUND, contains `CREATE TABLE IF NOT EXISTS disclosure_package_jobs`
- File `backend/app/ai_implementation/db/models.py` — FOUND, contains `class DisclosurePackageJob`
- File `backend/app/ai_implementation/db/__init__.py` — FOUND, re-exports `DisclosurePackageJob`
- File `backend/app/ai_implementation/seed/seed_database.py` — FOUND, contains "Old Mill Homeowners Association"
- File `backend/tests/conftest.py` — FOUND, contains all three new fixtures
- Commit `8e1413d` — FOUND in `git log`
- Commit `776673e` — FOUND in `git log`
- Commit `db7dd52` — FOUND in `git log`
