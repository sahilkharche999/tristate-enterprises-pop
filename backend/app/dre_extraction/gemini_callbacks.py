"""Gemini callback constructors for the DRE extraction pipeline.

``pipeline.run_dre_extraction`` accepts caller-supplied callbacks for the
two Gemini calls it makes (Step-1 page classification + Step-2 setup
extraction). This module builds those callbacks against the real
google-genai client so the live-test harness and the production
extraction service share a single construction path — preventing drift
between what the tests exercise and what the operator-triggered route
runs.

The functions are intentionally pure: pass a configured ``genai.Client``
plus the rendered-page-by-number lookup, get back two callables that
``run_dre_extraction`` knows how to invoke.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from .prompts import DRE_SETUP_EXTRACTOR_PROMPT
from .schemas import PageInventoryEntry
from .wire_schemas import WireDRESetupExtraction, WirePageInventoryBatch


def default_model_name() -> str:
    """Resolve the Gemini model from ``GEMINI_MODEL`` (matches live test)."""
    return os.environ.get("GEMINI_MODEL", "gemini-flash-latest")


def build_classify_callback(
    client: Any,
    *,
    model: str,
    rendered_pages_by_num: dict,
) -> Callable[[Any], list[PageInventoryEntry]]:
    """Construct the page-classification callback for ``classify_pages``.

    The callback is invoked once per ``PageBatch`` with one batch of page
    numbers; it sends the rendered PNG bytes for each page to Gemini and
    returns the parsed ``PageInventoryEntry`` list.
    """
    classification_prompt = (
        "Run Step 1 (page classification) ONLY of the following prompt — "
        "produce one page_inventory entry per page I send below. Do not "
        "extract anything else.\n\n"
        + DRE_SETUP_EXTRACTOR_PROMPT
    )

    def classify(batch):
        from google.genai import types

        parts = [types.Part.from_text(text=classification_prompt)]
        for page_num in batch.page_numbers:
            rendered = rendered_pages_by_num.get(page_num)
            if rendered is None:
                continue
            parts.append(
                types.Part.from_bytes(data=rendered.content, mime_type="image/png"),
            )
            parts.append(
                types.Part.from_text(text=f"(That was page {page_num} of the DRE.)"),
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
) -> Callable[[list[int]], tuple[str, Optional[WireDRESetupExtraction], dict]]:
    """Construct the Step-2 setup-extraction callback for ``run_dre_extraction``.

    Gemini receives the full prompt + PNG bytes for the
    ``relevant_pages`` set. Callers may pass an explicit ``max_pages``
    for test/debug runs, but production defaults to no silent cap.
    With ``response_schema`` attached,
    the SDK constrains output to ``WireDRESetupExtraction`` shape and
    returns the parsed instance via ``response.parsed``.

    The callback returns both the raw textual response (preserved
    verbatim for the audit log on ``DREExtractionRunRecord.raw_model_output``)
    AND the typed wire instance (or ``None`` if the SDK could not parse).
    The pipeline prefers the parsed instance on the happy path and falls
    back to text-parsing only if ``response.parsed`` is ``None``.
    """

    def extract(
        relevant_pages: list[int],
    ) -> tuple[str, Optional[WireDRESetupExtraction], dict]:
        from google.genai import types

        parts = [types.Part.from_text(text=DRE_SETUP_EXTRACTOR_PROMPT)]
        selected_pages = relevant_pages if max_pages is None else relevant_pages[:max_pages]
        for page_num in selected_pages:
            rendered = rendered_pages_by_num.get(page_num)
            if rendered is None:
                continue
            parts.append(
                types.Part.from_bytes(data=rendered.content, mime_type="image/png"),
            )
            parts.append(
                types.Part.from_text(text=f"(Page {page_num} of the DRE.)"),
            )

        response = client.models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=WireDRESetupExtraction,
            ),
        )
        parsed: Optional[WireDRESetupExtraction] = response.parsed
        audit = _extract_response_audit(response)
        return response.text or "", parsed, audit

    return extract


def _extract_response_audit(response: Any) -> dict:
    """Pull audit metadata off a ``GenerateContentResponse``.

    Returns a dict with ``model_version``, ``finish_reason``, and
    ``output_tokens``. Every field is best-effort: SDK shape can vary,
    and missing values fall back to empty strings / zero so the caller
    never has to guard.
    """
    model_version = ""
    try:
        mv = getattr(response, "model_version", None)
        if mv:
            model_version = str(mv)
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
    """JSON-repair callback for invalid Gemini responses.

    ``parse_extraction_response`` calls this once when either JSON
    parsing or Pydantic schema validation fails. The callback receives
    the original raw text PLUS the list of validation error strings so
    the repair prompt can ask Gemini to fix the specific drift.
    """

    def repair(prior_text: str, errors: list[str]) -> str:
        from google.genai import types

        error_block = "\n".join(f"- {e}" for e in (errors or [])) or "- (unspecified)"
        prompt = (
            "Your previous response failed validation. Return the SAME "
            "extracted content as a single valid JSON object — no "
            "markdown, no commentary, no preamble — and fix the issues "
            "listed below. Preserve every extracted value verbatim; only "
            "correct the JSON shape / types.\n\n"
            "Validation errors:\n"
            + error_block
            + "\n\nYour previous response:\n"
            + prior_text
        )
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
        return response.text or ""

    return repair


def gemini_client_from_env() -> Optional[Any]:
    """Return a configured ``genai.Client`` or ``None`` when API key is missing.

    Production callers should treat ``None`` as a misconfiguration and
    surface a 503 rather than running the pipeline against a stub.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    from google import genai

    return genai.Client(api_key=api_key)


__all__ = [
    "build_classify_callback",
    "build_extract_callback",
    "build_repair_callback",
    "default_model_name",
    "gemini_client_from_env",
]
