# Pre-Mortem Report

**Scope:** `backend/app/services/budget_history_service.py`, `backend/app/services/normalized_statement_workbook.py`, `backend/app/services/pdf_vlm_extractor.py`, `backend/app/services/reserve_study_extractor.py`, `backend/app/services/financial_document_router.py`, `backend/app/services/financial_statement_validation.py`, `backend/app/services/statement_period_inference.py`, and the parser contract in `backend/app/services/income_statement_parser.py`
**Date:** 2026-04-18

## Summary

The service layer is strongest where it validates outputs after extraction, but it is still carrying several hidden contracts between the PDF extractor, workbook normalizer, parser state machine, and reserve-study discovery heuristics. I found 6 high-plausibility future-failure scenarios. The dominant themes are stringly-typed schema coupling, load-bearing workbook layouts, and heuristics that silently assume a stable relationship between page order, section semantics, and duplicate rows.

## Post-Mortems

### 1. Friendly section labels silently reclassified half the statement

**Severity:** High
**Component:** `backend/app/services/normalized_statement_workbook.py::build_normalized_statement_workbook`, `backend/app/services/budget_history_service.py::_infer_category`, `backend/app/services/income_statement_parser.py::_match_section_header`
**Fragility type:** Stringly-typed contracts

#### What happened

Budget uploads started succeeding, but operating totals were wrong and reserve rows showed up in the editable operating section. Users saw reserve transfers become editable line items, while some income rows were reclassified as operating expenses after upload.

#### The change that caused it

A future developer cleaned up the normalized workbook output to make column A more human-readable. They replaced the canonical `section_kind` values written in `build_normalized_statement_workbook()` with `item.section_label` strings such as `"ASSESSMENT INCOME"` and `"GENERAL AND ADMINISTRATIVE"` so the intermediate workbook would look nicer during debugging.

#### Why it broke

The downstream classification contract is not enforced by types. `build_normalized_statement_workbook()` currently depends on writing one of four canonical strings into the `"Section"` column (`income`, `operating`, `reserve_income`, `reserve_expense`) at `backend/app/services/normalized_statement_workbook.py:83-105`. `budget_history_service._infer_category()` first accepts exact canonical values, then falls back to `income_statement_parser._match_section_header()` prefix matching at `backend/app/services/budget_history_service.py:505-529` and `backend/app/services/income_statement_parser.py:304-321`. Free-text section labels that look reasonable to a reader do not satisfy either contract consistently, so rows fall through to account-code fallback and drift into the wrong category.

#### How it was caught

This would likely escape unit tests unless a regression test explicitly round-tripped a PDF extraction through the normalized workbook and checked category preservation. The first visible symptom would be incorrect category totals or reserve rows becoming editable in the draft UI, not an exception.

#### Hardening suggestions

Add an assertion in `build_normalized_statement_workbook()` that every written section value is one of `SECTION_KINDS`.
Add a round-trip test that starts with canonical PDF line items, writes the normalized workbook, re-parses it through `_table_to_line_items()`, and verifies category equality row-by-row.
Replace the implicit string contract with a dedicated hidden metadata column or workbook manifest that explicitly stores canonical section kinds.

### 2. One extra PDF column shifted budget numbers into the wrong slots

**Severity:** Critical
**Component:** `backend/app/services/budget_history_service.py::create_upload`, `backend/app/services/normalized_statement_workbook.py::build_normalized_statement_workbook`
**Fragility type:** Load-bearing defaults

#### What happened

Uploaded PDF statements still produced drafts, but annual budgets and YTD actuals were obviously swapped or blank for some communities. The generated draft looked internally consistent enough to pass the normal upload flow, yet the proposed budget numbers were materially wrong.

#### The change that caused it

A future developer added a `"Source Page"` or `"Evidence"` column to the normalized workbook so reviewers could trace each line item back to the PDF. The edit looked local to `build_normalized_statement_workbook()` and did not touch the upload orchestration code.

#### Why it broke

The PDF path in `create_upload()` hardcodes `pdf_known_columns = {"ytd_actual": 6, "annual_budget": 9}` at `backend/app/services/budget_history_service.py:1195-1208`. That assumes the workbook emitted by `build_normalized_statement_workbook()` will always keep the current ten-column layout from `backend/app/services/normalized_statement_workbook.py:50-62`. If a new column is inserted before either numeric field, the `BudgetPipeline` receives stale column indices while the rest of the upload flow still trusts the resulting enriched workbook. This is a load-bearing layout contract with no schema versioning and no runtime verification that the header names still match the fixed indices.

#### How it was caught

This would probably surface as a data-quality incident rather than a hard failure. Some statements might still pass `validate_extracted_statement()` if non-zero values remain in the shifted columns, so the bug could silently produce incorrect drafts until a user compared the imported numbers to the original statement.

#### Hardening suggestions

Derive `known_columns` from the workbook header row instead of hardcoding numeric positions.
Add a post-normalization assertion that the expected header names are still present at the indices passed to `BudgetPipeline`.
Version the normalized workbook format explicitly and reject unknown layouts instead of assuming positional compatibility.

### 3. Cross-page deduplication dropped legitimate accounts from multi-page statements

**Severity:** High
**Component:** `backend/app/services/pdf_vlm_extractor.py::_extract_full_document`, `backend/app/services/financial_statement_validation.py::validate_extracted_statement`
**Fragility type:** Coincidental correctness

#### What happened

Some multi-page PDF statements imported with missing rows even though every page extracted successfully. The draft looked plausible, but one of two identically named rows disappeared whenever the statement repeated the same account label on different pages or sections.

#### The change that caused it

A future developer broadened the per-page extraction prompt to keep more continuation rows, or started processing statement packets that include both operating and reserve sections with overlapping account labels. No dedupe code was changed because the import flow still "looked" stable in tests using simpler statements.

#### Why it broke

`_extract_full_document()` deduplicates merged line items using only `(account_code_text, label)` and keeps whichever copy has more populated numeric fields at `backend/app/services/pdf_vlm_extractor.py:611-624`. That logic is only accidentally safe while each real account appears once across the extracted page set. It ignores `section_kind`, `page_number`, and whether two rows are complementary rather than redundant. A new document family with repeated labels across pages would trigger silent data loss before validation, because `validate_extracted_statement()` checks coverage and totals but does not know which original rows were dropped.

#### How it was caught

This would be difficult to detect automatically unless the dropped row changed a section subtotal enough to trip `subtotal_mismatch`. More likely, an accountant would notice that one repeated account was missing from the draft while the upload itself still reported success.

#### Hardening suggestions

Include `section_kind` and possibly `page_number` in the dedupe key, or only dedupe rows that are exact semantic duplicates.
When duplicates are merged, preserve both row provenances and merge complementary numeric fields instead of picking a single winner.
Add regression coverage for a statement where the same label appears on multiple pages with different semantics.

### 4. Scanned reserve statements were quietly treated as operating statements

**Severity:** High
**Component:** `backend/app/services/financial_document_router.py::choose_financial_document_route`, `backend/app/services/pdf_vlm_extractor.py::_extract_full_document`
**Fragility type:** Assumptions baked into data transformations

#### What happened

After the team added support for phone-photo uploads and scanned reserve packets, reserve income and reserve expense rows started landing in the operating budget. Users saw reserve items appear as editable operating expenses, and reserve totals in the draft stayed at zero.

#### The change that caused it

A future developer reasonably expanded the upload UI to accept image files for reserve-related workflows because `choose_financial_document_route()` already routes image MIME types to the VLM path at `backend/app/services/financial_document_router.py:68-75`. They assumed the scanned-document path would preserve the same semantic classification as text-based PDFs.

#### Why it broke

The scanned fallback in `_extract_full_document()` explicitly cannot detect reserve-vs-operating pages without text and therefore forces `is_reserve = False` for no-text-layer pages at `backend/app/services/pdf_vlm_extractor.py:504-517` and `531-538`. That assumption is safe only while scanned uploads are effectively limited to operating income statements. Once a future feature routes scanned reserve statements through the same path, the extractor will still succeed structurally but classify reserve rows with the operating prompt, producing plausible but wrong categories.

#### How it was caught

Tests focused on successful extraction would probably still pass because line items and numeric coverage would exist. The bug would show up in downstream category totals, reserve review screens, or user complaints that reserve study numbers never reached the reserve sections.

#### Hardening suggestions

Pass document role or user-selected intent into the extractor so scanned reserve uploads do not default to operating semantics.
Add a reserve-page classifier that can run on images alone before choosing the operating or reserve prompt.
Write a regression test for a no-text-layer reserve statement and assert that reserve rows do not land in `operating`.

### 5. Reserve-study discovery dropped continuation pages after a harmless prompt refactor

**Severity:** High
**Component:** `backend/app/services/reserve_study_extractor.py::_classify_page_batch`, `_build_relevant_sequences`, `_retain_best_sequence`
**Fragility type:** Implicit ordering dependencies

#### What happened

Reserve-study extraction began returning only the first page of multi-page component schedules. The UI still showed rows, but long schedules were truncated and capital planning missed components that appeared on continuation pages.

#### The change that caused it

A future developer adjusted the discovery prompt wording to improve classification accuracy on noisy PDFs, or upgraded the model and accepted a slightly different distribution of `same_table_as_previous` and `same_table_as_next` flags. The change passed shallow tests because single-page reserve schedules still worked.

#### Why it broke

Sequence construction in `_build_relevant_sequences()` depends on adjacency plus the LLM-provided continuation flags at `backend/app/services/reserve_study_extractor.py:248-277`. `_retain_best_sequence()` then discards all non-winning pages and `_trim_sequence_to_anchor_range()` further trims pages based on `adds_new_component_rows` and `is_duplicate_component_repeat_page` at `315-419`. The code has multiple stages that silently depend on a stable meaning for those classifier booleans, but there is no deterministic fallback that says "two adjacent reserve-table pages with compatible headers should stay together" if one flag flips during a future prompt refactor.

#### How it was caught

This would likely be caught late through missing rows in the reserve-study UI or by a user noticing that later components never appeared. Unless tests assert exact selected page ranges for a multi-page schedule, the failure mode is silent truncation rather than an exception.

#### Hardening suggestions

Add deterministic continuity checks based on shared visible headers or repeated table geometry, not just LLM booleans.
Create regression fixtures that assert the exact winning page sequence for at least one multi-page reserve study with context pages and duplicate repeat pages.
Emit a warning when later reserve-table pages are discarded from the winning sequence so silent truncation becomes observable.

### 6. Duplicate-row merging collapsed different reserve components into one

**Severity:** High
**Component:** `backend/app/services/reserve_study_extractor.py::_rows_are_merge_compatible`, `_merge_reserve_rows`, `_dedupe_reserve_rows`
**Fragility type:** Invisible invariants

#### What happened

Two distinct reserve components were merged into a single row, cutting the projected replacement cost roughly in half. The reserve-study extraction still returned a valid document, but the resulting budget draft understated upcoming capital work.

#### The change that caused it

A future developer improved normalization of `line_item` text to strip building names, phase suffixes, or punctuation so duplicates across repeated schedules would merge more often. The edit looked like a sensible cleanup to reduce noisy duplicate rows in extracted reserve studies.

#### Why it broke

`_rows_are_merge_compatible()` currently treats rows as mergeable when the normalized `line_item` matches and the visible numeric fields do not conflict at `backend/app/services/reserve_study_extractor.py:573-599`. `_merge_reserve_rows()` then keeps a primary row and fills missing values from the secondary row at `602-630`. The code assumes equal normalized names imply semantic identity, but that invariant is not enforced anywhere. If future normalization removes location-specific detail from rows like "Roof - Building A" and "Roof - Building B", the deduper will collapse separate assets into one because the merge key has no table id, location field, or provenance guard.

#### How it was caught

This would probably not trigger any extractor error. The most likely discovery path is an analyst comparing the extracted reserve-study rows to the PDF and noticing that one component disappeared, or a later budgeting discrepancy when planned reserve spending is too low.

#### Hardening suggestions

Restrict merging to rows proven to come from duplicate-repeat pages rather than any pages in the selected sequence.
Preserve all source-page and source-row provenance and require stronger evidence than normalized name equality before merging.
Add fixture coverage with two components that share the same base name but represent different locations or phases.

## Themes and Recommendations

Three systemic patterns kept showing up:

First, the ingestion pipeline relies on hidden schema contracts between modules that are maintained by comments rather than interfaces. The most fragile examples are the canonical section strings, normalized workbook column order, and parser expectations around the `"Section"` and `"Label"` headers. A small shared schema object or workbook manifest would harden several of these seams at once.

Second, many success paths are heuristic and only weakly observable. The PDF extractor, reserve-study page classifier, and reserve-row deduper can all produce structurally valid outputs that are semantically wrong. The service layer would benefit from stronger invariant checks after each stage, plus regression tests that verify exact category preservation, selected reserve-study page ranges, and duplicate-row behavior.

Third, several components encode business meaning into filenames, prompt outputs, and free-text labels. That is workable today, but it makes future refactors deceptively risky. Passing explicit document role, schema version, and provenance metadata through the pipeline would reduce the number of places where semantics have to be reconstructed from strings.
