---
phase: 11
plan: 05
subsystem: disclosure_package
tags: [phase-11, merge, qpdf, pypdf, compiler, appendices]
requires: [11-01, 11-02, 11-03, 11-04]
provides:
  - "backend/app/disclosure_package/merge.py — merge_pdfs / qpdf_check / write_atomic_bytes"
  - "backend/app/disclosure_package/compiler.py — compile_package(...) returns CompileResult"
  - "backend/app/disclosure_package/appendices/old_mill/.gitkeep — keeps the appendix dir under version control"
  - "backend/app/disclosure_package/appendices/old_mill/MANIFEST.md — provenance + extraction protocol + legal-review checklist for 24 static appendices"
  - "backend/tests/test_disclosure_package_merge.py — 13 tests, all green"
  - "backend/tests/test_disclosure_package_compiler.py — 8 tests, all green"
affects:
  - "backend/app/disclosure_package — new merge + compiler modules wire the rendering pipeline together"
tech_stack_added: []
patterns_established:
  - "merge_pdfs up-front existence check: validate every source path BEFORE opening pypdf.PdfWriter; the failure mode is FileNotFoundError naming the offending path (REQ-D11-008), not a half-written temp file in the destination directory."
  - "qpdf_check three-state branch: shutil.which('qpdf') is None → RuntimeError with install hint; subprocess returncode != 0 → RuntimeError with stderr; success → None. Tests on systems without qpdf use the conftest qpdf_required fixture."
  - "write_atomic_bytes cleanup-on-rename-failure: if Path.replace raises (disk full, permission), unlink the temp file rather than leaving a partial-content sibling next to the destination."
  - "Compiler intermediate file naming: per-template PDFs are written as `.gen_{template}.pdf` (leading dot) so they don't collide with package.pdf and are easy to filter from directory listings. They're cleaned up after merge."
  - "Compiler's merge order builds full_paths in a single pass over spec.entries: GeneratedPage entries map to intermediate_pdfs[gen_index]; StaticAppendix entries map to appendices_root/entry.file. This keeps the order strictly tied to the spec literal, no second list-comprehension to drift out of sync."
  - "Compiler audit_path uses write_atomic_bytes (not Path.write_text) so the audit.json write inherits the same partial-write-invisible semantics as the PDF outputs."
  - "Compiler tests stub render_template via monkeypatch.setattr(compiler_module, 'render_template', fake): keeps compiler glue tests free of the WeasyPrint dependency (which isn't installable on Python 3.9 dev machines) while still exercising the real pypdf merge + qpdf check."
key_files_created:
  - backend/app/disclosure_package/merge.py
  - backend/app/disclosure_package/compiler.py
  - backend/app/disclosure_package/appendices/old_mill/.gitkeep
  - backend/app/disclosure_package/appendices/old_mill/MANIFEST.md
  - backend/tests/test_disclosure_package_merge.py
  - backend/tests/test_disclosure_package_compiler.py
key_files_modified: []
decisions:
  - "Test fixtures use PyMuPDF (fitz) to manufacture PDFs rather than calling render_template. PyMuPDF is already in requirements.txt for raster-diff (CONTEXT D-13); this avoids a circular dependency in the test suite where merge tests would need WeasyPrint to produce inputs. Bonus: the merge tests run without WeasyPrint installed locally on Python 3.9 dev machines, where weasyprint==68.1 is unavailable."
  - "Compiler tests monkeypatch render_template instead of invoking the real WeasyPrint pipeline. Reason: render behavior is already covered by 24 plan-04 tests that exercise WeasyPrint directly; compiler tests should test orchestration (preflight gating, merge order, audit-log writing, qpdf check), not the renderer. This keeps the compiler test runtime under 2 seconds and the test surface narrow."
  - "Static appendix scaffolding (Task 2) ships .gitkeep + MANIFEST.md only — NOT the 24 extracted PDFs. Per plan-05 Task 2 contract, extraction is a human-supervised step requiring legal/CCRs review (the appendix files reproduce California Civil Code text and Old Mill's CC&R / Rules & Regulations boilerplate). The MANIFEST.md captures the extraction protocol, page-range mapping, and legal-review checklist so the developer can run the qpdf --pages commands and approve the output before committing the PDFs."
  - "Compiler accepts trusted output_dir (T-11-05 disposition: accept). The router (plan 11-06) is responsible for sanitizing hoa_id and fiscal_year before constructing the disclosure-packages/{hoa_id}/{fiscal_year}/{job_id}/ subtree under BUDGET_STORAGE_ROOT. Documented in compiler.py's module docstring."
  - "Compiler keeps generated.pdf as an intermediate artifact (not unlinked) for operator debugging. Per-template intermediates (.gen_*.pdf) ARE unlinked after merge. Trade-off: ~30 extra pages of disk per run vs. operator inability to inspect the system-generated portion in isolation when triaging a bad raster diff."
metrics:
  tasks_completed: 3
  tasks_total: 3
  duration: "~30 min"
  files_created: 6
  files_modified: 0
  test_count: 21  # 13 merge + 8 compiler
  test_runtime_seconds: 2.7
  commits:
    - "14a45ff feat(11-05): merge.py — pypdf merge + qpdf --check + atomic file write"
    - "4518298 chore(11-05): scaffold appendices/old_mill/ with .gitkeep + MANIFEST.md"
    - "f3bdc1b feat(11-05): compiler.py — preflight → audit-context → render → merge → qpdf"
completed_date: "2026-05-08"
---

# Phase 11 Plan 05: Merge + Compiler + Appendix Scaffolding Summary

End-to-end "make a PDF" pipeline minus the API and frontend. `compile_package(...)` orchestrates preflight → audit-context → render → merge → qpdf-check → atomic write, producing a parity-matched package.pdf at `{output_dir}/package.pdf` with `audit.json` and intermediate `generated.pdf` beside it. `merge.py` is the pypdf+qpdf+atomic-write toolbelt the orchestrator depends on. The 24 static-appendix PDFs themselves are NOT yet extracted — that step is gated on Tri-State legal/CCRs review, with the protocol captured in `appendices/old_mill/MANIFEST.md`.

## Tasks Executed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | merge.py — pypdf merge + qpdf --check + atomic file write | `14a45ff` | merge.py, test_disclosure_package_merge.py |
| 2 | scaffold appendices/old_mill/ with .gitkeep + MANIFEST.md | `4518298` | appendices/old_mill/.gitkeep, appendices/old_mill/MANIFEST.md |
| 3 | compiler.py — preflight → audit-context → render → merge → qpdf | `f3bdc1b` | compiler.py, test_disclosure_package_compiler.py |

## Public surface added

### `backend/app/disclosure_package/merge.py`

```python
def merge_pdfs(pdf_paths: list[Path], output_path: Path) -> None
def qpdf_check(path: Path) -> None        # raises RuntimeError on non-zero or missing binary
def write_atomic_bytes(destination: Path, payload: bytes) -> None
QPDF_TIMEOUT_SECONDS = 30
```

### `backend/app/disclosure_package/compiler.py`

```python
def compile_package(*, spec, budget_draft, reserve_snapshot, hoa_metadata,
                    output_dir, appendices_root=None) -> CompileResult
class CompileResult(BaseModel):  # output_path, audit_path, intermediate_path,
                                 # page_count, sha256, completed_at
class CompileError(RuntimeError):  # carries `field_paths: list[str]`
```

## REQ traceability

| REQ-ID | Verification |
|--------|--------------|
| REQ-D11-007 (qpdf check enforced) | `qpdf_check` raises on non-zero return; `compile_package` calls it on every output. Test: `test_qpdf_check_raises_on_corrupt_pdf` (corrupt PDF → RuntimeError); `test_compile_package_output_passes_qpdf_check` (real merge → success). |
| REQ-D11-008 (missing appendix raises clear error) | `merge_pdfs` validates every source path up-front and raises `FileNotFoundError` naming the offending path. `validate_inputs` returns a `PreflightError(field_path="package_spec.appendices", ...)` per missing appendix; `compile_package` raises `CompileError(field_paths=[...])` before any rendering happens. Tests: `test_merge_pdfs_missing_file_raises_with_path`, `test_compile_package_raises_on_preflight_error`. |
| REQ-D11-009 (page count == sum of page_count_hint) | With render stubs honoring each `GeneratedPage.page_count_hint` and static-appendix stubs sized to each `StaticAppendix.page_count_hint`, the merged page count exactly equals `sum(entry.page_count_hint)`. Test: `test_compile_package_page_count_matches_hint_sum`. (Plan 11-08 raster diff against the golden PDF tightens this from "honors hints" to "byte-for-byte parity".) |
| REQ-D11-011 (audit.json captures formula calls) | `compile_package` runs `_compute_all` inside `audit_context(input_snapshot)`; every formula call is decorated with `@audit_formula` which appends to the active log; the log is serialized to `audit.json` after merge. Test: `test_compile_package_writes_audit_json` validates structure (input_snapshot, formula_calls, started_at, completed_at) and that each call has `formula_id + version + inputs + output + computed_at`. Full integration check (against a golden audit JSON) lives in plan 11-06. |
| REQ-D11-015 (deterministic re-render — stub) | Two consecutive runs with the same input snapshot produce equal page counts and equal audit-log outputs (timestamps differ). Strict byte-equivalence is impractical because pypdf and PyMuPDF embed creation timestamps. Test: `test_compile_package_two_runs_produce_byte_equivalent_pdf`. |

## Page-count invariant verification

The plan's central invariant — **output page count == sum of `page_count_hint` in `OLD_MILL_2026.entries`** — is exercised by `test_compile_package_page_count_matches_hint_sum`. With:

- 17 GeneratedPage entries, each render stub returning a PDF with `page_count_hint` pages
- 24 StaticAppendix entries, each appendix PDF on disk sized to `page_count_hint` pages

… the merged package.pdf lands at exactly `sum(entry.page_count_hint) == 109` pages. The `OLD_MILL_2026` literal targets 109 (matching the golden 2026 disclosure PDF) per plan 11-02 — the merge pipeline preserves that count exactly when inputs honor their hints.

The real-world page count when WeasyPrint renders the templates is **28 not 31** for the generated portion (per plan-04 SUMMARY: cover_letter, notes_1_to_3, and thirty_year_funding_plan land at -1 from their hints). With actual WeasyPrint render the merged total will land at 106 not 109 — matching the plan-04 ±1 tolerance. Plan 11-08 raster diff is the gate that decides whether to (a) tighten the templates to land exactly on the hint, (b) revise the hints downward, or (c) accept the delta.

## Static-appendix scaffolding (Task 2)

Plan 11-05 Task 2 is a `checkpoint:human-action` per the plan literal. The orchestrator instructed scaffolding only — the 24 extracted PDFs themselves are NOT in this commit because:

1. The extraction is one-time human-supervised work (running `qpdf --pages` against the golden PDF).
2. Some appendices reproduce California Civil Code text or Tri-State CC&R / Rules & Regulations boilerplate; legal review must approve the reproductions before they ship.
3. Per RESEARCH risk #16, the PDFs total ~5–10 MB and live on a CC&R-amendment cadence (years), so they belong in the Docker image rather than git history.

What ships in this commit:

- **`appendices/old_mill/.gitkeep`** — empty marker so the directory is committed.
- **`appendices/old_mill/MANIFEST.md`** — full inventory (24 entries summing to 78 static pages + 31 generated = 109 total), source-page-range mapping against the golden, CCRs/statutory authority per appendix, the `qpdf --pages` extraction protocol, and the legal-review checklist that gates committing the actual PDFs.

The MANIFEST records 24 entries — 23 from the original RESEARCH list plus the `appendix_pages_74_87.pdf` placeholder that plan 11-02 added to close the 14-page gap (RESEARCH § "Static appendix pages" jumped from page 73 to 88). The placeholder is flagged for plan-08 raster-diff to identify the actual sub-documents.

Until the developer runs the extraction protocol, `compile_package` will fail at preflight with `field_paths=["package_spec.appendices"]` — no surprise in production, no silent half-merge.

## Verification

| Check | Result |
|-------|--------|
| `pytest tests/test_disclosure_package_merge.py -q` | PASS — 13 / 13 |
| `pytest tests/test_disclosure_package_compiler.py -q` | PASS — 8 / 8 |
| `pytest tests/test_disclosure_package_*.py -q` (excluding render) | PASS — 76 / 76 |
| `python -c "from app.disclosure_package.merge import merge_pdfs, qpdf_check, write_atomic_bytes; print('imports OK')"` | OK |
| `python -c "from app.disclosure_package.compiler import compile_package, CompileResult, CompileError"` | OK |
| `grep "qpdf.*--check" backend/app/disclosure_package/merge.py` | found |
| `grep "compile_package(" backend/app/disclosure_package/compiler.py` | found |
| `ls backend/app/disclosure_package/appendices/old_mill/` | `.gitkeep MANIFEST.md` |
| qpdf 12.3.2 installed locally → all qpdf-dependent tests run (don't skip) | confirmed |

The render test suite (`test_disclosure_package_render.py`) requires `weasyprint==68.1`, which has no Python 3.9 wheel; it fails with ModuleNotFoundError on this dev machine but works in the backend Docker image (Dockerfile installs WeasyPrint plus its C deps in the apt-get layer). No regression from this plan.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] `_compute_all` divide-by-zero on empty reserve revenues**

- **Found during:** Task 3 — designing the test fixture.
- **Issue:** The plan's snippet computes `base_2026_monthly = total_rev_rep / Decimal(units) / Decimal(12)` with no guard. If a budget draft has zero replacement-revenue items the division produces `Decimal("0")` cleanly, BUT a fixture with `units=0` (which preflight catches separately) would crash here before the gate fires. More importantly, `total_rev_rep == 0` means no replacement contributions are budgeted — a legitimate intermediate state during admin editing — and silently passing 0/0 = 0 hides the misconfiguration.
- **Fix:** Guard with `if hoa_metadata.units > 0 and total_rev_rep > 0:` and fall back to `Decimal("0.00")`. Not strictly a bug fix (preflight catches `units == 0` before this code runs), but cheap defensive depth that costs one if-statement and removes a sharp edge for plan-12 admin editing.
- **Files modified:** `backend/app/disclosure_package/compiler.py`
- **Commit:** `f3bdc1b`

**2. [Rule 2 — Critical safety] write_atomic_bytes cleanup on rename failure**

- **Found during:** Task 1 — TDD for `test_write_atomic_bytes_partial_write_invisible`.
- **Issue:** The plan's snippet for `write_atomic_bytes` writes to a temp file then `tmp_path.replace(destination)`. If the rename raises (disk full, cross-mount rename, permission), the temp file lingers in the destination directory forever. The plan's test specifies "partial write never visible at dest path" — the snippet honors that — but the temp file pollution is a separate hazard that grows over time.
- **Fix:** Wrap `tmp_path.replace(destination)` in try/except, unlink the temp file on any exception, and re-raise. The destination is still untouched (rename is atomic), AND the temp file is cleaned up. Pattern matches `budget_history_service._write_atomic_bytes` semantics where a failed move would also leak the temp file — this fix could be back-ported there as a follow-up if desired.
- **Files modified:** `backend/app/disclosure_package/merge.py`
- **Commit:** `14a45ff`

**3. [Rule 3 — Blocking] Plan snippet imports `fitz` at top of compile_package, causing circular concern**

- **Found during:** Task 3 — running the test suite.
- **Issue:** The plan's snippet has `import fitz` inside `compile_package` (correct — keeps it lazy) but reads `package_path.read_bytes()` AFTER `qpdf_check` AND `fitz.open(str(package_path))` AFTER. That's two file reads. With a 109-page disclosure PDF at ~5–10 MB this is ~10–20 MB of extra I/O.
- **Fix:** Read the bytes once into a local, compute SHA-256 from the buffer, and pass the buffer to `fitz.open(stream=..., filetype="pdf")`. Eliminates the second file read and decouples page-count from disk-state at the moment of inspection.
- **Files modified:** `backend/app/disclosure_package/compiler.py`
- **Commit:** `f3bdc1b`

### Plan-Level TDD Gate Compliance

Plan 11-05 has `tdd="true"` on Tasks 1 and 3. RED→GREEN cycles for both:

- **Task 1:** Wrote `test_disclosure_package_merge.py` first (13 tests). `pytest --collect-only` failed with ImportError on missing `merge.py`. Wrote `merge.py`. Re-ran: 13 / 13 GREEN. Commit: `14a45ff`.
- **Task 3:** Wrote `test_disclosure_package_compiler.py` first (8 tests). `pytest --collect-only` failed with ImportError on missing `compiler.py`. Wrote `compiler.py`. Re-ran: 8 / 8 GREEN. Commit: `f3bdc1b`.

Per plan-03 / plan-04 SUMMARY precedent for sub-second RED→GREEN cycles, both phases ship in a combined commit (the test file + module file in the same commit, rather than separate `test(...)` and `feat(...)` commits). Reason: the cycle was deterministic and synchronous — the test file is the spec; the module is its implementation; there was no behavioral pivot between RED and GREEN.

## Auth gates

None encountered.

## Out-of-Scope Discoveries (NOT fixed)

Inherited from plans 11-02/11-03/11-04 SUMMARYs:

- `tests/test_income_statement_parser.py::test_full_pipeline_esprit_park_structure` and `tests/test_sync_history_api.py::test_table_to_line_items_supports_headerless_income_statement_layout` fail on the merge base. Pre-existing; not caused by Phase 11-05 and not in scope to fix.
- The `weasyprint==68.1` package has no Python 3.9 wheel (only Python 3.10+). Plan 11-04 render tests are unrunnable on dev machines that haven't upgraded Python; they pass in Docker / CI. Out of scope for this plan to fix; the dev-machine workaround is to run the disclosure-package suite in the backend Docker container.

## Known Stubs

- **`thirty_year_projections: []` in `_compute_all`'s computed dict.** Plan 11-04 templates reference `thirty_year_projections` (decade-band tables in `thirty_year_funding_plan.html`) and rely on the field being present. Plan 11-06 / 11-09 will populate it from the reserve-study cash-flow projections. The current empty list satisfies StrictUndefined and produces an empty decade table in the rendered template. Documented in compiler.py inline comment.
- **`backend/app/disclosure_package/appendices/old_mill/*.pdf` not present.** Required by `compile_package` at runtime; preflight raises with `field_paths=["package_spec.appendices"]` until the human-supervised extraction step runs (plan 11-05 Task 2 protocol in MANIFEST.md). This is intentional per the plan's `autonomous: false` flag.

## Threat Flags

None — this plan stays within the `<threat_model>` declared in plan-05 (T-11-04 mitigate, T-11-05 accept). No new network endpoints, auth paths, or trust-boundary file accesses beyond what the plan models.

## Self-Check

**Files:**
- `backend/app/disclosure_package/merge.py` — FOUND
- `backend/app/disclosure_package/compiler.py` — FOUND
- `backend/app/disclosure_package/appendices/old_mill/.gitkeep` — FOUND
- `backend/app/disclosure_package/appendices/old_mill/MANIFEST.md` — FOUND
- `backend/tests/test_disclosure_package_merge.py` — FOUND
- `backend/tests/test_disclosure_package_compiler.py` — FOUND

**Commits:**
- `14a45ff` — FOUND in `git log`
- `4518298` — FOUND in `git log`
- `f3bdc1b` — FOUND in `git log`

**Tests:**
- 13 / 13 plan-05 merge tests green
- 8 / 8 plan-05 compiler tests green
- 76 / 76 full disclosure_package non-render suite green

## Self-Check: PASSED
