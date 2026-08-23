"""Unit tests for CC&R page admission (neighbor expansion) and pipeline wiring."""

from __future__ import annotations

from app.dre_extraction.schemas import PageInventoryEntry
from app.governing_doc_extraction.gemini_callbacks import _classify_instruction
from app.governing_doc_extraction.gemini_callbacks import build_repair_callback
from app.governing_doc_extraction.page_classification import (
    CCR_NEIGHBOR_SEED_PAGE_TYPES,
    CCR_PAGE_TYPE_LABELS,
    expand_relevant_pages_with_neighbors,
)
from app.governing_doc_extraction.pipeline import run_ccr_extraction
from app.governing_doc_extraction.prompts import CCR_POLICY_EXTRACTOR_PROMPT
from app.governing_doc_extraction.wire_schemas import WireCCRPolicyExtraction


def _entry(page: int, page_type: str) -> PageInventoryEntry:
    return PageInventoryEntry(
        page_number=page, page_type=page_type, confidence=0.9, notes=""
    )


class TestExpandRelevantPagesWithNeighbors:
    def test_adds_neighbors_of_assessment_only(self) -> None:
        inventory = [
            _entry(15, "assessment/allocation provisions"),
            _entry(54, "exhibit/percentage-interest table"),
        ]
        out = expand_relevant_pages_with_neighbors(
            inventory, [15, 54], page_count=65
        )
        assert out == [14, 15, 16, 54]

    def test_does_not_seed_definitions_or_exhibit(self) -> None:
        inventory = [
            _entry(8, "definitions"),
            _entry(9, "definitions"),
            _entry(54, "exhibit/percentage-interest table"),
        ]
        relevant = [8, 9, 54]
        out = expand_relevant_pages_with_neighbors(
            inventory, relevant, page_count=65
        )
        assert out == relevant

    def test_clamps_bounds(self) -> None:
        inventory = [_entry(1, "assessment/allocation provisions")]
        out = expand_relevant_pages_with_neighbors(
            inventory, [1], page_count=10
        )
        assert out == [1, 2]

        inventory_last = [_entry(10, "special assessment provisions")]
        out_last = expand_relevant_pages_with_neighbors(
            inventory_last, [10], page_count=10
        )
        assert out_last == [9, 10]

    def test_case_insensitive_seed_types(self) -> None:
        inventory = [
            _entry(5, " Assessment/Allocation Provisions "),
        ]
        out = expand_relevant_pages_with_neighbors(
            inventory, [5], page_count=20
        )
        assert out == [4, 5, 6]

    def test_empty(self) -> None:
        assert expand_relevant_pages_with_neighbors([], [], page_count=10) == []

    def test_idempotent(self) -> None:
        inventory = [_entry(15, "assessment/allocation provisions")]
        once = expand_relevant_pages_with_neighbors(
            inventory, [15], page_count=65
        )
        twice = expand_relevant_pages_with_neighbors(
            inventory, once, page_count=65
        )
        assert once == twice

    def test_seed_constant_matches_plan(self) -> None:
        assert "assessment/allocation provisions" in CCR_NEIGHBOR_SEED_PAGE_TYPES
        assert "special assessment provisions" in CCR_NEIGHBOR_SEED_PAGE_TYPES
        assert "definitions" not in CCR_NEIGHBOR_SEED_PAGE_TYPES
        assert "exhibit/percentage-interest table" not in CCR_NEIGHBOR_SEED_PAGE_TYPES


class TestClassifyInstruction:
    def test_mentions_continuation_guidance(self) -> None:
        text = _classify_instruction(CCR_PAGE_TYPE_LABELS)
        assert "assessment/allocation provisions" in text
        assert "continues" in text.lower() or "continuation" in text.lower() or "same Article" in text


def test_ccr_prompt_requires_context_complete_allocation_rules() -> None:
    assert "allocation_context" in CCR_POLICY_EXTRACTOR_PROMPT
    assert "special_assessment" in CCR_POLICY_EXTRACTOR_PROMPT
    assert "one cited rule" in CCR_POLICY_EXTRACTOR_PROMPT


def test_ccr_repair_uses_ccr_response_schema() -> None:
    class FakeModels:
        def __init__(self) -> None:
            self.configs = []

        def generate_content(self, **kwargs):
            self.configs.append(kwargs["config"])
            return type("Response", (), {"text": "{}"})()

    class FakeClient:
        def __init__(self) -> None:
            self.models = FakeModels()

    client = FakeClient()
    repair = build_repair_callback(client, model="test-model")

    repair("{}", ["missing field"])

    assert client.models.configs[0].response_schema is WireCCRPolicyExtraction


class TestCcrPipelinePageExpansion:
    def test_no_relevant_pages_blocks_generic_extraction(self) -> None:
        def classify(batch):
            return [_entry(page, "blank/irrelevant") for page in batch.page_numbers]

        def extract(_relevant):
            raise AssertionError("extraction must not run without admitted pages")

        record = run_ccr_extraction(
            page_count=2,
            classify_pages_callback=classify,
            extract_policy_callback=extract,
            model_name="test-model",
        )

        assert record.status == "extraction_partial"
        assert record.relevant_page_numbers == []
        assert any("relevant" in warning.lower() for warning in record.validation_warnings)

    def test_mislabeled_neighbor_reaches_extract(self) -> None:
        """p.16 mislabeled as enforcement still enters extract via ±1 expand."""

        def classify(batch):
            pages = []
            for n in batch.page_numbers:
                if n == 15:
                    pages.append(
                        _entry(15, "assessment/allocation provisions")
                    )
                elif n == 16:
                    pages.append(_entry(16, "enforcement/dispute resolution"))
                elif n == 54:
                    pages.append(
                        _entry(54, "exhibit/percentage-interest table")
                    )
                else:
                    pages.append(_entry(n, "blank/irrelevant"))
            return pages

        extracted_pages: list[list[int]] = []

        def extract(relevant: list[int]):
            extracted_pages.append(list(relevant))
            # Minimal domain-shaped dump via empty wire is hard; return empty
            # structured failure-friendly payload: use domain path with None wire
            # and valid JSON matching DRE domain loosely is complex.
            # Return a tiny valid domain extraction as raw JSON after to_domain
            # skip — pipeline needs parseable extraction. Use a simple
            # succeeded parse via wire_parsed=None and raw that fails → failed.
            # Instead supply a callback that returns wire-like domain already
            # converted... pipeline expects WireCCRPolicyExtraction optional.
            # Easiest: return raw that fails schema → status failed but we only
            # care that relevant pages expanded before extract is called.
            return ("{}", None, {})

        record = run_ccr_extraction(
            page_count=60,
            classify_pages_callback=classify,
            extract_policy_callback=extract,
            model_name="test-model",
        )
        assert extracted_pages, "extract callback was not invoked"
        pages = extracted_pages[0]
        assert 15 in pages
        assert 16 in pages  # neighbor of assessment page
        assert 54 in pages
        assert record.relevant_page_numbers == sorted(pages)
