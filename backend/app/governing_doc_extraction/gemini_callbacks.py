"""Gemini callback constructors for the CC&R / governing-document extraction pipeline.

Mirrors the pattern in ``dre_extraction.gemini_callbacks``:
  build_classify_callback  — Step-1: legal page classification
  build_extract_callback   — Step-2: allocation-policy extraction
  build_repair_callback    — single repair retry on parse/validation failure

The classification call uses a modified prompt instructing Gemini to assign
legal-document page-type labels (from CCR_PAGE_TYPE_LABELS) rather than the
DRE budget-oriented labels.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from app.config import settings
from app.dre_extraction.schemas import PageInventoryEntry
from app.dre_extraction.wire_schemas import WirePageInventoryBatch

from .page_classification import CCR_PAGE_TYPE_LABELS
from .prompts import CCR_POLICY_EXTRACTOR_PROMPT
from .wire_schemas import WireCCRPolicyExtraction


def default_model_name() -> str:
    return settings.GEMINI_MODEL or "gemini-flash-latest"


def _classify_instruction(label_list: list[str]) -> str:
    labels_formatted = "\n".join(f"  • {lbl}" for lbl in label_list)
    return (
        "You are classifying pages of a legal governing document (CC&R / Declaration).\n"
        "For each page I send, assign exactly one label from the list below that best "
        "describes the page's primary content. Return a page_inventory list with one "
        "entry per page.\n\n"
        "Labels:\n"
        + labels_formatted
        + "\n\nRespond only with the page_inventory JSON — no other text."
    )


def build_classify_callback(
    client: Any,
    *,
    model: str,
    rendered_pages_by_num: dict,
) -> Callable[[Any], list[PageInventoryEntry]]:
    """Build the Step-1 legal-page-classification callback."""
    classification_prompt = _classify_instruction(CCR_PAGE_TYPE_LABELS)

    def classify(batch: Any) -> list[PageInventoryEntry]:
        from google.genai import types

        parts = [types.Part.from_text(text=classification_prompt)]
        for page_num in batch.page_numbers:
            rendered = rendered_pages_by_num.get(page_num)
            if rendered is None:
                continue
            parts.append(
                types.Part.from_bytes(data=rendered.content, mime_type="image/png")
            )
            parts.append(
                types.Part.from_text(
                    text=f"(That was page {page_num} of the governing document.)"
                )
            )

        response = client.models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=WirePageInventoryBatch,
            ),
        )
        parsed: Optional[WirePageInventoryBatch] = response.parsed
        if parsed is None:
            return []
        out: list[PageInventoryEntry] = []
        for entry in parsed.page_inventory:
            data = {k: v for k, v in entry.model_dump().items() if v is not None}
            out.append(PageInventoryEntry(**data))
        return out

    return classify


def build_extract_callback(
    client: Any,
    *,
    model: str,
    rendered_pages_by_num: dict,
    max_pages: Optional[int] = None,
) -> Callable[[list[int]], tuple[str, Optional[WireCCRPolicyExtraction], dict]]:
    """Build the Step-2 allocation-policy extraction callback."""

    def extract(
        relevant_pages: list[int],
    ) -> tuple[str, Optional[WireCCRPolicyExtraction], dict]:
        from google.genai import types

        parts = [types.Part.from_text(text=CCR_POLICY_EXTRACTOR_PROMPT)]
        selected = relevant_pages if max_pages is None else relevant_pages[:max_pages]
        for page_num in selected:
            rendered = rendered_pages_by_num.get(page_num)
            if rendered is None:
                continue
            parts.append(
                types.Part.from_bytes(data=rendered.content, mime_type="image/png")
            )
            parts.append(
                types.Part.from_text(
                    text=f"(Page {page_num} of the governing document.)"
                )
            )

        response = client.models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=WireCCRPolicyExtraction,
            ),
        )
        raw: str = response.text or ""
        wire_parsed: Optional[WireCCRPolicyExtraction] = response.parsed
        audit = _extract_response_audit(response)
        return raw, wire_parsed, audit

    return extract


def _extract_response_audit(response: Any) -> dict:
    model_version = ""
    try:
        model_version = str(getattr(response, "model_version", "") or "")
    except Exception:
        pass

    finish_reason = ""
    try:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            fr = getattr(candidates[0], "finish_reason", None)
            if fr is not None:
                finish_reason = getattr(fr, "name", None) or str(fr)
    except Exception:
        pass

    output_tokens = 0
    try:
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            tok = getattr(usage, "candidates_token_count", None)
            if tok is not None:
                output_tokens = int(tok)
    except Exception:
        pass

    return {
        "model_version": model_version,
        "finish_reason": finish_reason,
        "output_tokens": output_tokens,
    }


def build_repair_callback(
    client: Any,
    *,
    model: str,
) -> Callable[[str, list[str]], str]:
    """JSON-repair callback — single retry on parse/validation failure."""

    def repair(prior_text: str, errors: list[str]) -> str:
        from google.genai import types

        error_block = "\n".join(f"- {e}" for e in (errors or [])) or "- (unspecified)"
        prompt = (
            "Your previous response failed validation. Return the SAME "
            "extracted content as a single valid JSON object — no markdown, "
            "no commentary — and fix the issues listed below. Preserve every "
            "extracted value verbatim; only correct the JSON shape / types.\n\n"
            "Validation errors:\n"
            + error_block
            + "\n\nYour previous response:\n"
            + prior_text
        )
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Content(
                    role="user", parts=[types.Part.from_text(text=prompt)]
                )
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
        return response.text or ""

    return repair


def gemini_client_from_env() -> Optional[Any]:
    """Return a configured genai.Client or None when the API key is missing."""
    from app.dre_extraction.gemini_callbacks import gemini_client_from_env as _base
    return _base()


__all__ = [
    "build_classify_callback",
    "build_extract_callback",
    "build_repair_callback",
    "default_model_name",
    "gemini_client_from_env",
]
