"""Google Gemini client wrapper with structured output and retry logic."""
import asyncio
import logging
from typing import Optional, Any

from google import genai
from google.genai import types, errors
from pydantic import BaseModel

from ...config import settings

logger = logging.getLogger(__name__)

# Module-level singleton
_gemini_client: Optional[genai.Client] = None


def get_llm_client() -> genai.Client:
    """Return the module-level Gemini client, creating it on first use."""
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _gemini_client


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

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=response_schema,
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
            # response.parsed is auto-populated by SDK when response_schema is Pydantic
            if response.parsed is not None:
                return response.parsed
            # Fallback: manual validation from response.text (safety net per Research pitfall 3)
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
            logger.error("Gemini server error: %s", e)
            break
        except asyncio.TimeoutError:
            logger.warning("Gemini call timed out after %.1fs", timeout)
            return None
        except Exception as e:
            logger.error("Unexpected Gemini error: %s", e)
            break

    logger.error("All Gemini retry attempts exhausted")
    return None


async def call_llm_vision(
    messages: list[dict],
    response_schema: type[BaseModel],
    temperature: float = 0.0,
    timeout: float = 60.0,
) -> Optional[Any]:
    """Call Gemini with text + images in one request. Returns parsed Pydantic instance.

    Per D-11: Hybrid ingestion — text parts provide exact numerics, image parts
    provide visual/spatial context.
    Per D-12: Full document in a single call (no per-page splitting).
    """
    client = get_llm_client()
    backoff_delays = [2, 4, 8]

    system_text = None
    content_parts: list = []
    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"]
            continue
        content = msg.get("content")
        if isinstance(content, str):
            content_parts.append(types.Part.from_text(text=content))
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    content_parts.append(types.Part.from_text(text=part["text"]))
                elif part.get("type") == "image":
                    # Image bytes from render_pdf_pages
                    if part.get("data") is not None:
                        content_parts.append(
                            types.Part.from_bytes(
                                data=part["data"],
                                mime_type=part.get("mime_type", "image/png"),
                            )
                        )

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=response_schema,
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
            if response.parsed is not None:
                return response.parsed
            if response.text:
                return response_schema.model_validate_json(response.text)
            return None
        except errors.ClientError as e:
            if e.code == 429:
                wait = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                logger.warning("Rate limited, waiting %ds (attempt %d)...", wait, attempt + 1)
            else:
                logger.error("Gemini vision API error: %s", e)
                break
        except errors.ServerError as e:
            logger.error("Gemini server error: %s", e)
            break
        except asyncio.TimeoutError:
            logger.warning("Gemini vision call timed out after %.1fs", timeout)
            return None
        except Exception as e:
            logger.error("Unexpected Gemini vision error: %s", e)
            break

    logger.error("All Gemini vision retry attempts exhausted")
    return None
