"""Google Gemini client wrapper with structured output and retry logic."""
import asyncio
import json
import logging
import re
import weakref
from typing import Optional, Any

from google import genai
from google.genai import types, errors
from pydantic import BaseModel

from ...config import settings

logger = logging.getLogger(__name__)


def _strip_unsupported_schema_keys(schema: dict) -> dict:
    """Recursively strip JSON Schema keys that Gemini doesn't support.

    Gemini's controlled generation rejects:
    - additionalProperties
    - $defs / $ref
    - default values
    - minLength / maxLength on strings
    - minItems / maxItems on arrays
    - ge / le / gt / lt constraints
    """
    UNSUPPORTED = {
        "additionalProperties", "$defs", "default", "title",
        "minLength", "maxLength", "minItems", "maxItems",
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    }
    if not isinstance(schema, dict):
        return schema
    cleaned = {}
    for key, value in schema.items():
        if key in UNSUPPORTED:
            continue
        if key == "$ref":
            continue
        if isinstance(value, dict):
            cleaned[key] = _strip_unsupported_schema_keys(value)
        elif isinstance(value, list):
            cleaned[key] = [
                _strip_unsupported_schema_keys(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def _make_gemini_schema(response_schema: type[BaseModel]) -> dict:
    """Convert a Pydantic model to a Gemini-compatible JSON schema dict."""
    raw = response_schema.model_json_schema()

    # Inline $defs references
    defs = raw.pop("$defs", {})
    schema_str = json.dumps(raw)
    for def_name, def_schema in defs.items():
        ref_str = f'{{"$ref": "#/$defs/{def_name}"}}'
        replacement = json.dumps(_strip_unsupported_schema_keys(def_schema))
        schema_str = schema_str.replace(ref_str, replacement)
    schema = json.loads(schema_str)

    return _strip_unsupported_schema_keys(schema)


def _compact_log_value(value: object) -> str:
    return " ".join(str(value or "").split())


def _vision_log_context(
    *,
    response_schema: type[BaseModel],
    text_parts: list[str],
    image_count: int,
    image_bytes: int,
) -> str:
    joined_text = "\n".join(text_parts[:8])
    filename_match = re.search(r"Filename:\s*([^\n]+)", joined_text)
    filename = _compact_log_value(filename_match.group(1)) if filename_match else "unknown"
    schema_name = response_schema.__name__
    purpose_by_schema = {
        "_ReserveStudyBatchClassification": "reserve_discovery",
        "ExtractedReserveStudyPage": "reserve_extract_page",
        "ExtractedFinancialStatementPage": "budget_extract_page",
        "StatementPageSelection": "budget_page_selection",
    }
    purpose = purpose_by_schema.get(schema_name, schema_name)
    pages = []
    for pattern in (r"\bPage:\s*(\d+)", r"\bPDF page\s+(\d+)", r"\bImage for Page:\s*(\d+)"):
        pages.extend(re.findall(pattern, joined_text, flags=re.IGNORECASE))
    unique_pages = sorted({int(page) for page in pages}) if pages else []
    pages_text = ",".join(str(page) for page in unique_pages) if unique_pages else "unknown"
    return (
        f"purpose={purpose} schema={schema_name} filename={filename} pages={pages_text} "
        f"image_count={image_count} image_bytes={image_bytes}"
    )

# Per-event-loop client cache.
#
# Why not a simple module-level singleton: the genai.Client's async surface
# (client.aio) lazily creates an aiohttp connection pool bound to the event
# loop it first touches. If a loop closes (e.g. a ThreadPoolExecutor thread
# finishes after running asyncio.run) and a later caller on a DIFFERENT loop
# tries to reuse the singleton, the first in-flight request on the new loop
# raises "Event loop is closed" because the SDK's internal session state is
# still bound to the dead loop.
#
# Concrete reproducer in this codebase: LLM column detection runs inside a
# ThreadPoolExecutor with its own short-lived loop; the FastAPI request
# handler then fires parallel PDF page extractions on the main loop via
# asyncio.gather. Page 1 eats the cross-loop error; pages 2+ work because
# the SDK has rebuilt its internal session on the current loop by then.
#
# Why WeakKeyDictionary instead of dict[id(loop), ...]: Python's id() is only
# unique among LIVE objects. After a loop is garbage-collected, a future loop
# allocated at the same memory address would collide with the stale cache
# entry and inherit its dead internal session. WeakKeyDictionary auto-removes
# entries when the loop is GC'd, eliminating this class of bug.
_gemini_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, genai.Client]" = weakref.WeakKeyDictionary()
# Fallback client used when get_llm_client is called outside any running loop
# (sync context). A separate slot so it never competes with per-loop entries.
_gemini_client_no_loop: Optional[genai.Client] = None


def _require_gemini_config() -> None:
    """Fail fast with a clear message when Gemini env is not set.

    Raised at call time rather than import time so unrelated tests/tools can
    import modules that transitively reference llm_client without needing a
    live API key.
    """
    if not settings.GEMINI_MODEL:
        raise RuntimeError(
            "GEMINI_MODEL is not set. Add it to your .env or environment. "
            "Deployment policy belongs in env, not in source."
        )
    if settings.GOOGLE_GENAI_USE_ENTERPRISE:
        if not settings.GOOGLE_CLOUD_PROJECT:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT is not set. Add it to your .env or environment "
                "when GOOGLE_GENAI_USE_ENTERPRISE=true."
            )
        if not settings.GOOGLE_CLOUD_LOCATION:
            raise RuntimeError(
                "GOOGLE_CLOUD_LOCATION is not set. Add it to your .env or environment "
                "when GOOGLE_GENAI_USE_ENTERPRISE=true."
            )
        return
    if not settings.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to your .env or environment. "
            "See .env.example for the expected format."
        )


def _create_gemini_client() -> genai.Client:
    """Create a Gemini client using either Vertex/ADC or API-key auth."""
    if settings.GOOGLE_GENAI_USE_ENTERPRISE:
        return genai.Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )
    return genai.Client(api_key=settings.GEMINI_API_KEY)


def get_llm_client() -> genai.Client:
    """Return a Gemini client cached per event loop.

    The cache is a WeakKeyDictionary keyed on the running event loop
    object itself. Two consequences:

    1. Each event loop gets its own client, so a client created on loop A
       is never reused from loop B (eliminates 'Event loop is closed').
    2. When a loop is garbage-collected, its cache entry disappears
       automatically (no stale entries, no leak).

    See the module comment above _gemini_clients for the full rationale.
    """
    global _gemini_client_no_loop
    _require_gemini_config()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (sync call path). Cache in a dedicated slot.
        if _gemini_client_no_loop is None:
            _gemini_client_no_loop = _create_gemini_client()
        return _gemini_client_no_loop
    cached = _gemini_clients.get(loop)
    if cached is None:
        cached = _create_gemini_client()
        _gemini_clients[loop] = cached
    return cached


async def call_llm(
    messages: list[dict],
    response_schema: type[BaseModel],
    temperature: float = 0.3,
    timeout: float = 10.0,
) -> Optional[Any]:
    """Call Gemini API with controlled generation. Returns parsed Pydantic instance.

    Per D-04: Uses response_schema for schema-enforced output at decode level.
    Per D-06: Only rate-limit retry with exponential backoff (no validation-retry).
    """
    client = get_llm_client()
    backoff_delays = [2, 4, 8]

    # Build contents: extract system instruction, collect user parts
    system_text = None
    user_parts = []
    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"]
        else:
            user_parts.append(types.Part.from_text(text=msg["content"]))

    gemini_schema = _make_gemini_schema(response_schema)

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=gemini_schema,
        temperature=temperature,
        system_instruction=system_text,
    )

    for attempt, delay in enumerate([0] + backoff_delays):
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=user_parts,
                    config=config,
                ),
                timeout=timeout,
            )
            parsed = getattr(response, "parsed", None)
            if parsed is not None:
                if isinstance(parsed, response_schema):
                    return parsed
                return response_schema.model_validate(parsed)
            if response.text:
                return response_schema.model_validate_json(response.text)
            return None
        except errors.ClientError as e:
            if e.code == 429:
                wait = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                logger.warning("Rate limited, waiting %ds (attempt %d)...", wait, attempt + 1)
            else:
                logger.error("Gemini API client error: %s", e)
                break
        except errors.ServerError as e:
            wait = backoff_delays[min(attempt, len(backoff_delays) - 1)]
            logger.warning("Gemini server error (503/5xx), retrying in %ds (attempt %d)...: %s", wait, attempt + 1, e)
        except asyncio.TimeoutError:
            # Retry transient slowness via the same backoff loop as 429/503.
            # Previously returned None immediately, giving the call a single
            # shot — which silently dropped pages whose first call happened
            # to be slow (e.g. cold start, dense page, network jitter).
            if attempt >= len(backoff_delays):
                logger.error("Gemini call timed out after %.1fs on final attempt (attempt %d)", timeout, attempt + 1)
                return None
            wait = backoff_delays[min(attempt, len(backoff_delays) - 1)]
            logger.warning("Gemini call timed out after %.1fs, retrying in %ds (attempt %d)...", timeout, wait, attempt + 1)
        except Exception as e:
            logger.error("Unexpected Gemini error: %s", e)
            break

    logger.error("All Gemini retry attempts exhausted")
    return None


async def call_llm_vision(
    messages: list[dict],
    response_schema: type[BaseModel],
    temperature: float = 0.0,
    timeout: float = 120.0,
) -> Optional[Any]:
    """Call Gemini with text + images in one request. Returns parsed Pydantic instance.

    Per D-11: Hybrid ingestion — text parts provide exact numerics, image parts
    provide visual/spatial context.
    Per D-12: Full document in a single call (no per-page splitting).

    Default timeout is 120s (up from 60s): vision calls ship a full-page PNG
    plus the page text, and Gemini routinely approaches 60s on dense operating
    statements. 120s gives comfortable headroom while still bounding the call.
    """
    client = get_llm_client()
    backoff_delays = [2, 4, 8]

    system_text = None
    content_parts: list = []
    text_parts_for_log: list[str] = []
    image_count = 0
    image_bytes = 0
    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"]
            continue
        content = msg.get("content")
        if isinstance(content, str):
            text_parts_for_log.append(content)
            content_parts.append(types.Part.from_text(text=content))
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    text_parts_for_log.append(str(part["text"]))
                    content_parts.append(types.Part.from_text(text=part["text"]))
                elif part.get("type") == "image":
                    # Image bytes from render_pdf_pages
                    if part.get("data") is not None:
                        data = part["data"]
                        image_count += 1
                        image_bytes += len(data) if hasattr(data, "__len__") else 0
                        content_parts.append(
                            types.Part.from_bytes(
                                data=data,
                                mime_type=part.get("mime_type", "image/png"),
                            )
                        )
    log_context = _vision_log_context(
        response_schema=response_schema,
        text_parts=text_parts_for_log,
        image_count=image_count,
        image_bytes=image_bytes,
    )

    gemini_schema = _make_gemini_schema(response_schema)

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=gemini_schema,
        temperature=temperature,
        system_instruction=system_text,
    )

    for attempt, delay in enumerate([0] + backoff_delays):
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=content_parts,
                    config=config,
                ),
                timeout=timeout,
            )
            if response.text:
                logger.info("Gemini vision call succeeded: %s", log_context)
                return response_schema.model_validate_json(response.text)
            logger.warning("Gemini vision call returned empty response: %s", log_context)
            return None
        except errors.ClientError as e:
            if e.code == 429:
                wait = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                logger.warning("Gemini vision rate limited, waiting %ds (attempt %d): %s", wait, attempt + 1, log_context)
            else:
                logger.error("Gemini vision API error: %s context=%s", e, log_context)
                break
        except errors.ServerError as e:
            wait = backoff_delays[min(attempt, len(backoff_delays) - 1)]
            logger.warning(
                "Gemini vision server error (503/5xx), retrying in %ds (attempt %d): %s context=%s",
                wait,
                attempt + 1,
                e,
                log_context,
            )
        except asyncio.TimeoutError:
            # Retry transient slowness via the same backoff loop as 429/503.
            # Previously returned None immediately — which silently dropped
            # pages whose first call happened to be slow (cold start, dense
            # page, network jitter). Partial extraction then propagated
            # downstream as if it were complete. See also the strict-mode
            # guard in pdf_vlm_extractor._extract_full_document which refuses
            # partial results even if a page fails all retries here.
            if attempt >= len(backoff_delays):
                logger.error(
                    "Gemini vision call timed out after %.1fs on final attempt (attempt %d): %s",
                    timeout,
                    attempt + 1,
                    log_context,
                )
                return None
            wait = backoff_delays[min(attempt, len(backoff_delays) - 1)]
            logger.warning(
                "Gemini vision call timed out after %.1fs, retrying in %ds (attempt %d): %s",
                timeout,
                wait,
                attempt + 1,
                log_context,
            )
        except Exception as e:
            logger.error("Unexpected Gemini vision error: %s context=%s", e, log_context)
            break

    logger.error("All Gemini vision retry attempts exhausted: %s", log_context)
    return None
