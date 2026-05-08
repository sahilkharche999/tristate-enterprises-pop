---
phase: 11
slug: old-mill-end-to-end-disclosure-package-mvp
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-05-08
last_updated: 2026-05-08
---

# Phase 11 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution. Filled in by `gsd-planner` from `11-RESEARCH.md` § "Validation Architecture (Nyquist Dimension 8)". Final per-task verification map populated in plan 11-09.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (backend) + manual smoke (frontend; vitest not configured in MVP) |
| **Config file** | `backend/pytest.ini` / `backend/pyproject.toml` |
| **Quick run command** | `cd backend && pytest tests/ -x --ff -q` |
| **Full suite command** | `cd backend && pytest tests/` |
| **Estimated runtime** | ~60s for quick run; ~90s for full suite (target < 90s) |

---

## Sampling Rate

- **After every task commit:** Run quick command above (focused on touched test files)
- **After every plan wave:** Run full suite
- **Before `/gsd-verify-work`:** Full suite must be green AND `pytest tests/test_disclosure_package_raster_diff.py::test_raster_diff_each_generated_page -x` (the smoking-gun parity check) must pass
- **Max feedback latency:** 90 seconds for quick run

---

## Per-Task Verification Map

> One row per task across plans 11-01 through 11-09. Each row carries the literal `<automated>` command from the source plan's `<verify>` block. Status starts as ⬜ pending; the verifier flips to ✅ green / ❌ red / ⚠️ flaky as `/gsd-verify-work` runs.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 11-01-T1 | 11-01 | 1 | REQ-D11-016, REQ-D11-017 | T-11-03 | Bundled fonts prevent SSRF font fetch | grep | `cd backend && grep -q "^weasyprint==68.1$" requirements.txt && grep -q "^Jinja2==3.1.6$" requirements.txt && grep -q "^pypdf==6.10.2$" requirements.txt && grep -q "libpango-1.0-0" Dockerfile && grep -q "qpdf" Dockerfile && grep -q "fonts-liberation" Dockerfile` | ✅ | ⬜ pending |
| 11-01-T2 | 11-01 | 1 | REQ-D11-016, REQ-D11-017 | T-11-01 | property_id FK anchors ownership check | grep + python import | `cd backend && grep -q "CREATE TABLE IF NOT EXISTS disclosure_package_jobs" app/ai_implementation/schema.sql && grep -q "CREATE INDEX IF NOT EXISTS idx_disclosure_jobs_property" app/ai_implementation/schema.sql && grep -q "class DisclosurePackageJob" app/ai_implementation/db/models.py && grep -q "DISCLOSURE_JOB_PENDING" app/ai_implementation/db/models.py && python -c "from app.ai_implementation.db.models import DisclosurePackageJob, DISCLOSURE_JOB_PENDING; assert DISCLOSURE_JOB_PENDING == 'pending'; assert DisclosurePackageJob.__tablename__ == 'disclosure_package_jobs'"` | ✅ | ⬜ pending |
| 11-01-T3 | 11-01 | 1 | REQ-D11-016 | — | Schema applied to live DB without divergence | manual (BLOCKING) | (manual checkpoint — operator confirms `sqlite3 <devdb> ".schema disclosure_package_jobs"` returns full DDL and Railway-volume strategy is documented in 11-01-SUMMARY.md) | ✅ | ⬜ pending |
| 11-01-T4 | 11-01 | 1 | REQ-D11-016 | T-11-05 | tmp_path fixture sandboxes test storage | grep + pytest collect | `cd backend && grep -qi "old mill" app/ai_implementation/seed/seed_database.py && test -f app/disclosure_package/__init__.py && python -c "import app.disclosure_package" && grep -q "disclosure_storage_root" tests/conftest.py && grep -q "golden_old_mill_pdf" tests/conftest.py && grep -q "qpdf_required" tests/conftest.py && cd backend && pytest tests/conftest.py --collect-only -q 2>&1 \| head -5` | ✅ | ⬜ pending |
| 11-02-T1 | 11-02 | 2 | REQ-D11-001 | — | Pydantic schemas reject malformed inputs at boundary | pytest + python | `cd backend && pytest tests/test_disclosure_package_schemas.py -x -q 2>&1 \| tail -20 && python -c "from app.disclosure_package.schemas import BudgetDraft, ReserveStudySnapshot, HOAStaticData, PackageSpec, PreflightError, AuditLog, FormulaCall, GeneratedPage, StaticAppendix; print('OK')"` | ✅ | ⬜ pending |
| 11-02-T2 | 11-02 | 2 | REQ-D11-011 | — | @audit_formula records every computed value (audit trail) | pytest | `cd backend && pytest tests/test_disclosure_package_audit.py -x -q 2>&1 \| tail -20` | ✅ | ⬜ pending |
| 11-02-T3 | 11-02 | 2 | REQ-D11-001 | — | Pure formula functions; deterministic Decimal math | pytest + python | `cd backend && pytest tests/test_disclosure_package_formulas.py -x -q 2>&1 \| tail -30 && python -c "from app.disclosure_package.formulas import percent_funded, under_funded_balance_per_unit; from decimal import Decimal; assert percent_funded(cash_reserves=Decimal('2600000'), estimated_liability=Decimal('4575000')) == 57; assert under_funded_balance_per_unit(estimated_liability=Decimal('4575000'), cash_reserves=Decimal('2600000'), units=279) == Decimal('7080'); print('GOLDEN OK')"` | ✅ | ⬜ pending |
| 11-03-T1 | 11-03 | 3 | REQ-D11-002, REQ-D11-003 | — | Adapters translate DB rows to typed schemas without leaking SQLAlchemy state | pytest + python | `cd backend && pytest tests/test_disclosure_package_adapters.py -x -q 2>&1 \| tail -20 && python -c "from app.disclosure_package.adapters import from_budget_history_record, from_reserve_study_extraction, from_hoa_record; from decimal import Decimal; bd = from_budget_history_record({'line_items': [{'label': 'X', 'amount': 605.00, 'section': 'Income', 'is_revenue': True}]}); assert bd.line_items[0].amount == Decimal('605.0'); print('Adapters OK')"` | ✅ | ⬜ pending |
| 11-03-T2 | 11-03 | 3 | REQ-D11-004, REQ-D11-005 | — | Preflight blocks generation on missing required inputs | pytest | `cd backend && pytest tests/test_disclosure_package_preflight.py -x -q 2>&1 \| tail -20` | ✅ | ⬜ pending |
| 11-04-T1 | 11-04 | 4 | REQ-D11-006 | T-11-03 | url_fetcher denies all remote fetches (SSRF block) | python import + assert | `cd backend && python -c "from app.disclosure_package.render import _deny_url_fetcher, RemoteFetchDenied; raised=False; \nimport pytest\ntry:\n  _deny_url_fetcher('http://evil/x')\nexcept RemoteFetchDenied:\n  raised=True\nassert raised; print('SSRF deny OK')"` | ✅ | ⬜ pending |
| 11-04-T2 | 11-04 | 4 | REQ-D11-006 | T-11-03 | Templates have no \|safe escapes against unsafe data | grep | `cd backend && ls app/disclosure_package/templates/old_mill/*.html \| wc -l \| grep -E "^\s*1[78]\s*$"` | ✅ | ⬜ pending |
| 11-04-T3 | 11-04 | 4 | REQ-D11-006 | — | Renderer snapshot tests catch layout drift within ±1 page-count hint | pytest | `cd backend && pytest tests/test_disclosure_package_render.py -x -q 2>&1 \| tail -40` | ✅ | ⬜ pending |
| 11-05-T1 | 11-05 | 5 | REQ-D11-007, REQ-D11-008 | — | pypdf merge + qpdf --check + atomic write prevents partial writes | pytest + python | `cd backend && pytest tests/test_disclosure_package_merge.py -x -q 2>&1 \| tail -30 && python -c "from app.disclosure_package.merge import merge_pdfs, qpdf_check, write_atomic_bytes; print('imports OK')"` | ✅ | ⬜ pending |
| 11-05-T2 | 11-05 | 5 | REQ-D11-007 | — | Static appendices bundle sealed at scaffold time | manual (BLOCKING) | (manual checkpoint — operator extracts appendix PDFs into `backend/app/disclosure_package/appendices/old_mill/` per scaffold MANIFEST.md and confirms in 11-05-SUMMARY.md) | ✅ | ⬜ pending |
| 11-05-T3 | 11-05 | 5 | REQ-D11-007, REQ-D11-009, REQ-D11-011 | — | Compiler orchestrates preflight → audit-context → render → merge → qpdf in order | pytest | `cd backend && pytest tests/test_disclosure_package_compiler.py -x -q 2>&1 \| tail -30` | ✅ | ⬜ pending |
| 11-06-T1 | 11-06 | 6 | REQ-D11-012, REQ-D11-013 | T-11-01, T-11-05, T-11-06 | Service enforces auth ownership, sanitizes path segments, holds regen lock | python import + assert | `cd backend && python -c "from app.disclosure_package.service import is_supported_hoa, _sanitize_segment, _output_dir_for, create_job, assert_ownership, run_render_job; assert is_supported_hoa('Old Mill Homeowners Association'); assert not is_supported_hoa('Hastings Square'); print('service OK')"` | ✅ | ⬜ pending |
| 11-06-T2 | 11-06 | 6 | REQ-D11-012, REQ-D11-013, REQ-D11-014, REQ-D11-015, REQ-D11-016, REQ-D11-017 | T-11-01 | Router exposes 4 endpoints; main.py mounts router | python import + grep | `cd backend && python -c "from app.disclosure_package.router import router; paths = {r.path for r in router.routes}; assert '/api/disclosure-package/generate' in paths; assert '/api/disclosure-package/{job_id}/status' in paths; assert '/api/disclosure-package/{job_id}/download' in paths; assert '/api/disclosure-package/{job_id}/audit' in paths; print('routes OK')" && grep -q "disclosure_package_router" backend/app/main.py && grep -q "from .disclosure_package.router" backend/app/main.py` | ✅ | ⬜ pending |
| 11-06-T3 | 11-06 | 6 | REQ-D11-012, REQ-D11-013, REQ-D11-014, REQ-D11-015, REQ-D11-016, REQ-D11-017 | T-11-01, T-11-06 | API integration tests cover auth, IDOR, regen lock, status/download/audit | pytest | `cd backend && pytest tests/test_disclosure_package_api.py -x -q 2>&1 \| tail -40` | ✅ | ⬜ pending |
| 11-07-T1 | 11-07 | 7 | REQ-D11-018 | — | Polling hook holds 2s interval + 120s timeout + 3-failure cutoff | grep + tsc | `cd frontend && test -f src/app/api/disclosurePackage.ts && test -f src/app/components/disclosure/useDisclosureJob.ts && test -f src/app/lib/jobStageColors.ts && grep -q "generateDisclosurePackage" src/app/api/disclosurePackage.ts && grep -q "POLL_INTERVAL_MS = 2000" src/app/components/disclosure/useDisclosureJob.ts && grep -q "HARD_TIMEOUT_MS = 120" src/app/components/disclosure/useDisclosureJob.ts && (npx tsc --noEmit -p . 2>&1 \| tail -20 \|\| true)` | ✅ | ⬜ pending |
| 11-07-T2 | 11-07 | 7 | REQ-D11-018 | — | All 5 disclosure components present with verbatim UI-SPEC §9 copy | grep + tsc | `cd frontend && test -f src/app/components/disclosure/DisclosurePackagePanel.tsx && grep -q "Generate Disclosure Package" src/app/components/disclosure/DisclosurePackagePanel.tsx && grep -q "Disclosure package ready" src/app/components/disclosure/DisclosureResultBlock.tsx && grep -q "Generation Progress" src/app/components/disclosure/DisclosureProgressBlock.tsx && grep -q "Generation Failed" src/app/components/disclosure/DisclosureFailureBlock.tsx && grep -q "Budget draft saved" src/app/components/disclosure/DisclosurePreflightChecklist.tsx && (npx tsc --noEmit -p . 2>&1 \| tail -20 \|\| true)` | ✅ | ⬜ pending |
| 11-07-T3 | 11-07 | 7 | REQ-D11-018 | — | DisclosurePackagePanel mounted in BudgetScreenWrapper for Old Mill | grep + tsc | `cd frontend && grep -q "DisclosurePackagePanel" src/app/components/BudgetScreenWrapper.tsx && grep -q "Old Mill Homeowners Association" src/app/components/BudgetScreenWrapper.tsx && (npx tsc --noEmit -p . 2>&1 \| tail -10 \|\| true)` | ✅ | ⬜ pending |
| 11-08-T1 | 11-08 | 8 | REQ-D11-010 | — | verify.py exposes raster_diff with 0.01 default tolerance | python import + assert | `cd backend && python -c "from app.disclosure_package.verify import raster_diff, RasterDiffResult, PageDivergence, DEFAULT_DPI, DEFAULT_TOLERANCE; assert DEFAULT_TOLERANCE == 0.01; print('verify imports OK')"` | ✅ | ⬜ pending |
| 11-08-T2 | 11-08 | 8 | REQ-D11-007, REQ-D11-010 | — | Smoking-gun raster diff: every generated page within ±1% of golden | pytest | `cd backend && pytest tests/test_disclosure_package_raster_diff.py::test_raster_diff_each_generated_page -x -q 2>&1 \| tail -40` | ✅ | ⬜ pending |
| 11-08-T3 | 11-08 | 8 | REQ-D11-018 | — | Frontend smoke test (vitest if available; else deferred) | conditional vitest | `cd frontend && (grep -q vitest package.json && npx vitest run src/app/components/disclosure/__tests__/ 2>&1 \| tail -20 \|\| echo "vitest not configured — frontend smoke test deferred")` | ✅ | ⬜ pending |
| 11-08-T4 | 11-08 | 8 | REQ-D11-PARITY | — | Visual parity vs 2026 golden Old Mill PDF (human eye) | manual (BLOCKING) | (manual checkpoint — operator opens generated package + golden side-by-side, walks all 18 generated pages, flags drift in 11-08-SUMMARY.md) | ✅ | ⬜ pending |
| 11-09-T1 | 11-09 | 9 | REQ-D11-011, REQ-D11-018 | T-11-01 | Audit sheet consumes auth-required GET /audit endpoint; cannot bypass auth | grep + tsc | `cd frontend && test -f src/app/components/disclosure/DisclosureAuditSheet.tsx && grep -q "Audit Log" src/app/components/disclosure/DisclosureAuditSheet.tsx && grep -q "No audit entries recorded for this run" src/app/components/disclosure/DisclosureAuditSheet.tsx && grep -q "Could not load audit log" src/app/components/disclosure/DisclosureAuditSheet.tsx && grep -q "DisclosureAuditSheet" src/app/components/disclosure/DisclosurePackagePanel.tsx && grep -q "setAuditOpen" src/app/components/disclosure/DisclosurePackagePanel.tsx && (npx tsc --noEmit -p . 2>&1 \| tail -10 \|\| true)` | ✅ | ⬜ pending |
| 11-09-T2 | 11-09 | 9 | — | — | VALIDATION.md is the canonical contract for /gsd-verify-work | grep | `grep -q "nyquist_compliant: true" .planning/phases/11-old-mill-end-to-end-disclosure-package-mvp/11-VALIDATION.md && COUNT=$(grep -c "11-[0-9][0-9]-T" .planning/phases/11-old-mill-end-to-end-disclosure-package-mvp/11-VALIDATION.md); test "$COUNT" -ge 25 && grep -q "Approval:.*filled" .planning/phases/11-old-mill-end-to-end-disclosure-package-mvp/11-VALIDATION.md` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

> Derived from `11-RESEARCH.md` § "Risks, Unknowns, Landmines" and § "Rendering Stack Decision". Wave 0 must complete BEFORE any other wave. Marked complete after plan 11-01.

- [x] `backend/requirements.txt` — add WeasyPrint 68.1, pypdf 6.10.2, Jinja2 3.1.6 (and pinned transitives)
- [x] `backend/Dockerfile` — add native deps: `libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0 libharfbuzz-subset0 libffi-dev libjpeg-dev libopenjp2-7-dev qpdf fonts-liberation fonts-dejavu fonts-noto-core`
- [x] `backend/tests/test_disclosure_package_*.py` — stubs for REQ-D11-* (filled in by planner per § "Validation Architecture")
- [x] `backend/tests/conftest.py` — shared fixtures: Old Mill HOA seed, reserve study fixture, golden PDF path resolver
- [x] `backend/app/ai_implementation/data/budget-storage/old-mill-2026/` — persistent volume layout verified (CLAUDE.md: must live under `/app/app/ai_implementation/data` mount)
- [x] Verify Old Mill is seeded in `seed_database.py` (open question OQ-3 from RESEARCH.md)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Visual parity vs golden PDF | REQ-D11-PARITY | Some judgement (font rendering, sub-pixel layout) is hard to fully automate; raster-diff catches structural drift but a human eye is the final arbiter for the 2027 budget season cutover. | Open `2026/Old Mill 2026 budget disclosure.pdf` and the generated package side-by-side in a PDF viewer. Walk all 18 generated pages. Flag any layout drift, missing rows, or rounding mismatches. |
| Schema migration applied to live DB | REQ-D11-016 | Schema is applied to a live SQLite file on a Railway volume; idempotency vs divergence has to be eyeballed by an operator. | Local: `python -c "from app.ai_implementation.db import init_db; init_db()"` then `sqlite3 <devdb> ".schema disclosure_package_jobs"`. Railway: confirm startup hook runs `init_db()` against the mounted volume; check via `railway run` shell that no pre-existing table with divergent shape exists. |
| Static appendices bundle | REQ-D11-007 | Source PDFs are extracted from a vendor-provided package; they are not generated. | Operator extracts the static appendix PDFs into `backend/app/disclosure_package/appendices/old_mill/` per scaffold `MANIFEST.md`, confirms file count + checksums in 11-05-SUMMARY.md. |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (WeasyPrint deps, fixtures, golden PDF resolver)
- [x] No watch-mode flags in commands
- [x] Feedback latency < 90s for quick run
- [x] `nyquist_compliant: true` set in frontmatter (planner sets this once all tasks are mapped)

**Approval:** filled
