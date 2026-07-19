"""Tests for the DRE extraction pipeline (Phase 3 of dre-driven-assessment-engine).

Covers:
- Adapter: prompt-vocab → internal data model
- Validation: JSON parse retry, schema validation, citation audit,
  low-confidence flagging, DRE-value-preservation warnings
- Page classification: batching, inventory merge, filter to relevant
  page types
- Prompts: SHA-256 stamps stable, text non-empty
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.dre_extraction import (
    DEFAULT_BATCH_SIZE,
    EXTRACTION_RELEVANT_PAGE_TYPES,
    SINGLE_CALL_PAGE_THRESHOLD,
    DRESetupExtraction,
    ExtractionParseError,
    PageBatch,
    PageInventoryEntry,
    audit_entity_citations,
    classify_pages,
    collect_low_confidence_flags,
    collect_validation_warnings,
    filter_relevant_pages,
    map_allocation_method,
    merge_inventory,
    parse_extraction_response,
    split_pages_into_batches,
)
from app.dre_extraction.prompts import (
    BUDGET_POOL_MAPPER_PROMPT,
    BUDGET_POOL_MAPPER_PROMPT_SHA256,
    DRE_SETUP_EXTRACTOR_PROMPT,
    DRE_SETUP_EXTRACTOR_PROMPT_SHA256,
)


# -- prompt artifacts ------------------------------------------------------


class TestPromptArtifacts:
    def test_prompt1_text_loaded(self) -> None:
        assert "DRE (Department of Real Estate)" in DRE_SETUP_EXTRACTOR_PROMPT
        assert "STEP 1: Page classification" in DRE_SETUP_EXTRACTOR_PROMPT
        # Prompt v2.0+ delegates output-shape teaching to the wire schema's
        # ``response_schema``; the prompt itself only carries behavior +
        # procedural recipe. Anchor on the final procedural step instead
        # of the deleted OUTPUT FORMAT section.
        assert "STEP 10: Recommend a saved-setup template" in DRE_SETUP_EXTRACTOR_PROMPT
        # Behavioral invariants that MUST survive any future prompt rewrite.
        assert "DRE values are the law" in DRE_SETUP_EXTRACTOR_PROMPT
        assert "Never auto-correct" in DRE_SETUP_EXTRACTOR_PROMPT
        # v2.1: final completeness self-check anchor — guards against
        # accidental removal of the multi-pool gate in future rewrites.
        assert "STEP 11" in DRE_SETUP_EXTRACTOR_PROMPT
        assert "self-check" in DRE_SETUP_EXTRACTOR_PROMPT.lower()
        # v2.2: component-sub-pool extraction + multi-factor unit rule.
        assert "Component sub-pool extraction" in DRE_SETUP_EXTRACTOR_PROMPT
        assert "pool_factors" in DRE_SETUP_EXTRACTOR_PROMPT
        assert "parent_pool_key" in DRE_SETUP_EXTRACTOR_PROMPT
        # v2.2.1: completeness / no-sampling injunction. Guards against
        # the regression where the model emitted only the first few
        # units of a long unit summary table.
        assert "COMPLETENESS" in DRE_SETUP_EXTRACTOR_PROMPT
        assert "never sample" in DRE_SETUP_EXTRACTOR_PROMPT.lower()
        # v2.2.2: four review-driven rule additions.
        assert "verbatim string visible on a specific source page" in DRE_SETUP_EXTRACTOR_PROMPT
        assert "pending operator decision" in DRE_SETUP_EXTRACTOR_PROMPT
        assert "Per-component budget-line attribution" in DRE_SETUP_EXTRACTOR_PROMPT
        assert "Component-pool requirement" in DRE_SETUP_EXTRACTOR_PROMPT

    def test_prompt2_text_loaded(self) -> None:
        assert "Annual Budget" in BUDGET_POOL_MAPPER_PROMPT or "annual" in BUDGET_POOL_MAPPER_PROMPT
        assert "pool_key" in BUDGET_POOL_MAPPER_PROMPT

    def test_sha256_stamps_are_deterministic_hex(self) -> None:
        assert len(DRE_SETUP_EXTRACTOR_PROMPT_SHA256) == 64
        assert len(BUDGET_POOL_MAPPER_PROMPT_SHA256) == 64
        int(DRE_SETUP_EXTRACTOR_PROMPT_SHA256, 16)  # must be hex
        int(BUDGET_POOL_MAPPER_PROMPT_SHA256, 16)


# -- enum adapter ----------------------------------------------------------



class TestAllocationMethodAdapter:
    def test_equal_passthrough(self) -> None:
        r = map_allocation_method("equal")
        assert r.internal_method == "equal"
        assert r.forced_scope is None

    def test_square_footage_passthrough(self) -> None:
        r = map_allocation_method("square_footage")
        assert r.internal_method == "square_footage"

    def test_category_collapses_to_ownership_percentage(self) -> None:
        r = map_allocation_method("category")
        assert r.internal_method == "ownership_percentage"

    def test_parking_space_becomes_equal_over_parking_users(self) -> None:
        r = map_allocation_method("parking_space")
        assert r.internal_method == "equal"
        assert r.forced_scope == "parking_users"

    def test_custom_factor_becomes_square_footage_manual_denominator(self) -> None:
        r = map_allocation_method("custom_factor")
        assert r.internal_method == "square_footage"
        assert r.forced_denominator_source == "manual"
        assert r.needs_review

    def test_unknown_leaves_draft(self) -> None:
        r = map_allocation_method("unknown")
        assert r.internal_method is None
        assert r.needs_review


# -- validation: parse + schema -------------------------------------------


def _minimal_extraction_dict() -> dict:
    """The smallest payload that schema-validates as a DRESetupExtraction."""
    return {
        "document_metadata": {
            "association_name": "Test HOA",
            "confidence": 0.95,
            "source_pages": [1, 2],
        },
        "assessment_setup": {
            "setup_type": "fixed_equal",
            "confidence": 0.9,
            "source_pages": [3],
        },
    }


class TestParseExtractionResponse:
    def test_valid_json_parses(self) -> None:
        raw = json.dumps(_minimal_extraction_dict())
        result = parse_extraction_response(raw)
        assert result.succeeded
        assert result.repair_attempts == 0
        assert result.extraction is not None
        assert result.extraction.document_metadata.association_name == "Test HOA"

    def test_invalid_json_triggers_repair_callback(self) -> None:
        bad_raw = "{not valid json"
        calls: list[tuple[str, list[str]]] = []

        def repair(raw: str, errors: list[str]) -> str:
            calls.append((raw, errors))
            return json.dumps(_minimal_extraction_dict())

        result = parse_extraction_response(bad_raw, repair_callback=repair)
        assert len(calls) == 1
        assert result.succeeded
        assert result.repair_attempts == 1

    def test_repair_failure_raises(self) -> None:
        bad_raw = "{not valid json"
        repair_text = "still not valid"

        def repair(raw: str, errors: list[str]) -> str:
            return repair_text

        with pytest.raises(ExtractionParseError) as ctx:
            parse_extraction_response(bad_raw, repair_callback=repair)
        assert ctx.value.parse_result.repair_attempts == 1
        assert ctx.value.parse_result.schema_validation_errors

    def test_no_callback_raises_immediately_on_invalid_json(self) -> None:
        with pytest.raises(ExtractionParseError):
            parse_extraction_response("garbage")

    def test_schema_violation_collected_with_path(self) -> None:
        # Missing required setup_type field
        payload = _minimal_extraction_dict()
        del payload["assessment_setup"]["setup_type"]
        with pytest.raises(ExtractionParseError) as ctx:
            parse_extraction_response(json.dumps(payload))
        errors = ctx.value.parse_result.schema_validation_errors
        assert any("setup_type" in e for e in errors)

    def test_unknown_extra_keys_silently_dropped(self) -> None:
        # Extra top-level key shouldn't fail validation
        payload = _minimal_extraction_dict()
        payload["future_experimental_field"] = {"foo": "bar"}
        result = parse_extraction_response(json.dumps(payload))
        assert result.succeeded


# -- validation: citation audit -------------------------------------------


class TestCitationAudit:
    def test_complete_citations_pass(self) -> None:
        payload = _minimal_extraction_dict()
        payload["allocation_pools"] = [
            {
                "pool_key": "equal",
                "allocation_method": "equal",
                "source_pages": [5],
                "confidence": 0.9,
            }
        ]
        payload["unit_structure"] = {
            "groups": [
                {"group_id": "G1", "label": "G1", "source_page": 4, "confidence": 0.9},
            ]
        }
        extraction = DRESetupExtraction.model_validate(payload)
        audit = audit_entity_citations(extraction)
        assert not audit.is_partial
        assert audit.missing_citations == []

    def test_missing_document_pages_flagged(self) -> None:
        payload = _minimal_extraction_dict()
        payload["document_metadata"]["source_pages"] = []
        extraction = DRESetupExtraction.model_validate(payload)
        audit = audit_entity_citations(extraction)
        assert audit.is_partial
        assert any("document_metadata" in c for c in audit.missing_citations)

    def test_pool_without_source_pages_flagged(self) -> None:
        payload = _minimal_extraction_dict()
        payload["allocation_pools"] = [
            {
                "pool_key": "equal",
                "allocation_method": "equal",
                "source_pages": [],  # missing
                "confidence": 0.9,
            }
        ]
        extraction = DRESetupExtraction.model_validate(payload)
        audit = audit_entity_citations(extraction)
        assert audit.is_partial
        assert any("allocation_pools[0]" in c for c in audit.missing_citations)

    def test_group_without_source_page_flagged(self) -> None:
        payload = _minimal_extraction_dict()
        payload["unit_structure"] = {
            "groups": [
                {"group_id": "G1", "label": "G1", "confidence": 0.9},  # no source_page
            ]
        }
        extraction = DRESetupExtraction.model_validate(payload)
        audit = audit_entity_citations(extraction)
        assert audit.is_partial
        assert any("groups[0]" in c for c in audit.missing_citations)


# -- validation: low-confidence flagging ----------------------------------


class TestLowConfidenceFlags:
    def test_high_confidence_no_flags(self) -> None:
        payload = _minimal_extraction_dict()
        extraction = DRESetupExtraction.model_validate(payload)
        flags = collect_low_confidence_flags(extraction)
        assert flags == []

    def test_low_doc_confidence_flagged(self) -> None:
        payload = _minimal_extraction_dict()
        payload["document_metadata"]["confidence"] = 0.4
        extraction = DRESetupExtraction.model_validate(payload)
        flags = collect_low_confidence_flags(extraction)
        assert any("document_metadata" in f.path for f in flags)
        assert all(f.confidence < 0.7 for f in flags)

    def test_low_pool_confidence_flagged(self) -> None:
        payload = _minimal_extraction_dict()
        payload["allocation_pools"] = [
            {
                "pool_key": "shaky",
                "allocation_method": "equal",
                "source_pages": [1],
                "confidence": 0.5,
            }
        ]
        extraction = DRESetupExtraction.model_validate(payload)
        flags = collect_low_confidence_flags(extraction)
        assert any("allocation_pools[0]" in f.path for f in flags)


# -- validation: DRE value preservation warnings ---------------------------


class TestValidationWarnings:
    def test_fail_status_emits_warning(self) -> None:
        payload = _minimal_extraction_dict()
        payload["validation_checks"] = [
            {
                "check_name": "denominator_recalc",
                "status": "fail",
                "details": "DRE shows 157536 but rows sum to 157,500",
                "source_pages": [9],
            }
        ]
        extraction = DRESetupExtraction.model_validate(payload)
        warnings = collect_validation_warnings(extraction)
        assert len(warnings) == 1
        assert "denominator_recalc" in warnings[0]

    def test_passed_or_warning_status_no_warning_emitted(self) -> None:
        payload = _minimal_extraction_dict()
        payload["validation_checks"] = [
            {"check_name": "p1", "status": "pass", "details": "", "source_pages": []},
            {"check_name": "w1", "status": "warning", "details": "", "source_pages": []},
            {"check_name": "na1", "status": "not_applicable", "details": "", "source_pages": []},
        ]
        extraction = DRESetupExtraction.model_validate(payload)
        # Only fail-status emits warning rows (the others surface in the UI separately)
        assert collect_validation_warnings(extraction) == []


# -- page classification --------------------------------------------------


class TestSplitPagesIntoBatches:
    def test_below_threshold_single_batch(self) -> None:
        batches = split_pages_into_batches(50)
        assert len(batches) == 1
        assert batches[0].page_numbers == list(range(1, 51))

    def test_above_threshold_multiple_batches(self) -> None:
        batches = split_pages_into_batches(200)
        # 200 / 15 = 13.33 → 14 batches with default batch size 15
        assert len(batches) == 14
        assert batches[0].page_numbers == list(range(1, 16))
        assert batches[-1].page_numbers[-1] == 200

    def test_custom_batch_size(self) -> None:
        batches = split_pages_into_batches(100, batch_size=20)
        # 100/20 = 5 batches; but threshold is 50 so 100 triggers batching
        assert len(batches) == 5
        assert batches[0].page_numbers == list(range(1, 21))

    def test_zero_pages_empty(self) -> None:
        assert split_pages_into_batches(0) == []

    def test_invalid_batch_size_rejected(self) -> None:
        with pytest.raises(ValueError):
            split_pages_into_batches(200, batch_size=0)


class TestMergeInventory:
    def test_concat_and_sort_by_page(self) -> None:
        a = [PageInventoryEntry(page_number=2, page_type="cover")]
        b = [PageInventoryEntry(page_number=1, page_type="unit summary")]
        merged = merge_inventory([a, b])
        assert [e.page_number for e in merged] == [1, 2]

    def test_first_occurrence_wins_for_duplicates(self) -> None:
        a = [PageInventoryEntry(page_number=1, page_type="cover")]
        b = [PageInventoryEntry(page_number=1, page_type="unit summary")]
        merged = merge_inventory([a, b])
        assert merged[0].page_type == "cover"


class TestFilterRelevantPages:
    def test_filters_to_extraction_relevant_types(self) -> None:
        inventory = [
            PageInventoryEntry(page_number=1, page_type="cover/general information"),
            PageInventoryEntry(page_number=2, page_type="unit summary"),
            PageInventoryEntry(page_number=3, page_type="signature/certification"),
            PageInventoryEntry(page_number=4, page_type="proration schedule"),
            PageInventoryEntry(page_number=5, page_type="blank/irrelevant"),
        ]
        rel = filter_relevant_pages(inventory)
        assert rel == [2, 4]

    def test_case_insensitive_match(self) -> None:
        inventory = [
            PageInventoryEntry(page_number=1, page_type="  Unit Summary  "),
        ]
        assert filter_relevant_pages(inventory) == [1]


class TestClassifyPages:
    def test_drives_callback_per_batch(self) -> None:
        calls: list[PageBatch] = []

        def fake_classify(batch: PageBatch) -> list[PageInventoryEntry]:
            calls.append(batch)
            return [
                PageInventoryEntry(
                    page_number=p,
                    page_type="unit summary" if p % 2 == 0 else "cover/general information",
                )
                for p in batch.page_numbers
            ]

        result = classify_pages(60, fake_classify, batch_size=10)
        # 60/10 = 6 batches
        assert len(calls) == 6
        # Inventory has all 60 pages
        assert len(result.full_inventory) == 60
        # Filtered list = even page numbers (the "unit summary" ones)
        assert result.relevant_page_numbers == [p for p in range(2, 61, 2)]

    def test_below_threshold_one_call(self) -> None:
        calls: list[PageBatch] = []

        def fake_classify(batch: PageBatch) -> list[PageInventoryEntry]:
            calls.append(batch)
            return [
                PageInventoryEntry(page_number=p, page_type="unit summary")
                for p in batch.page_numbers
            ]

        result = classify_pages(30, fake_classify)
        assert len(calls) == 1
        assert len(result.full_inventory) == 30
        assert len(result.relevant_page_numbers) == 30

    def test_270_page_synthetic_classifies_without_per_call_bloat(self) -> None:
        """Per Phase 3.6 task: a 270-page synthetic DRE classifies in
        ≤20-page batches without any single Gemini call exceeding the
        batch limit. Pages 1–30 are cover/blank noise; pages 31–270 are
        a mix of extraction-relevant types.
        """
        seen_batch_sizes: list[int] = []

        def fake_classify(batch: PageBatch) -> list[PageInventoryEntry]:
            seen_batch_sizes.append(len(batch))
            entries: list[PageInventoryEntry] = []
            for p in batch.page_numbers:
                if p <= 30:
                    entries.append(PageInventoryEntry(page_number=p, page_type="cover/general information"))
                elif p <= 100:
                    entries.append(PageInventoryEntry(page_number=p, page_type="unit summary"))
                elif p <= 200:
                    entries.append(PageInventoryEntry(page_number=p, page_type="proration schedule"))
                else:
                    entries.append(PageInventoryEntry(page_number=p, page_type="blank/irrelevant"))
            return entries

        result = classify_pages(270, fake_classify, batch_size=20)
        # Every batch must be ≤ 20 pages
        assert all(size <= 20 for size in seen_batch_sizes)
        # Full inventory captures all 270 pages
        assert len(result.full_inventory) == 270
        # Filtered pages = 31–200 (relevant), = 170 pages
        assert result.relevant_page_numbers == list(range(31, 201))


# -- module constants ------------------------------------------------------


class TestModuleConstants:
    def test_single_call_threshold_is_50(self) -> None:
        assert SINGLE_CALL_PAGE_THRESHOLD == 50

    def test_default_batch_size_in_range(self) -> None:
        assert 10 <= DEFAULT_BATCH_SIZE <= 20

    def test_extraction_relevant_types_includes_key_types(self) -> None:
        # Sanity: the page types listed in Prompt 1 STEP 1 that obviously
        # carry extraction signal must be in the relevant set.
        for needed in ("unit summary", "annual operating budget", "proration schedule"):
            assert needed in EXTRACTION_RELEVANT_PAGE_TYPES


class TestDocumentMetadataCoercion:
    """Gemini occasionally returns ``null`` or a dict for free-text leaf
    fields where the prompt specs a string (notably ``preparer`` and
    ``location`` — legacy 1990s DREs commonly omit the preparer).
    ``DocumentMetadata`` must accept all three shapes without raising.
    """

    def test_null_preparer_coerces_to_empty_string(self) -> None:
        from app.dre_extraction.schemas import DocumentMetadata

        meta = DocumentMetadata.model_validate({"preparer": None})
        assert meta.preparer == ""

    def test_dict_preparer_flattens_to_string(self) -> None:
        from app.dre_extraction.schemas import DocumentMetadata

        meta = DocumentMetadata.model_validate(
            {"preparer": {"name": "Acme CPA", "address": "1 Main"}}
        )
        assert "Acme CPA" in meta.preparer
        assert "1 Main" in meta.preparer

    def test_null_location_and_document_title_coerce(self) -> None:
        from app.dre_extraction.schemas import DocumentMetadata

        meta = DocumentMetadata.model_validate(
            {"location": None, "document_title": None}
        )
        assert meta.location == ""
        assert meta.document_title == ""
