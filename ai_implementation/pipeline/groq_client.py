"""AsyncGroq client wrapper with retry logic and graceful fallback."""
import asyncio
import json
import logging
from typing import Optional, Any
import groq
from groq import AsyncGroq
from pydantic import BaseModel, ValidationError

from ..config import settings

logger = logging.getLogger(__name__)


def get_groq_client() -> AsyncGroq:
    """Initialize AsyncGroq client with API key from config."""
    return AsyncGroq(api_key=settings.GROQ_API_KEY)


async def call_groq(
    messages: list[dict],
    response_schema: type[BaseModel],
    temperature: float = 0.3,
    timeout: float = 10.0,
) -> Optional[Any]:
    """Call Groq API and return parsed Pydantic model instance.

    Retry logic:
    - Pydantic validation failure → retry once with temperature=0
    - Rate limit (429) → exponential backoff: 2s, 4s, 8s
    - Total failure → return None (caller handles degradation)
    """
    client = get_groq_client()
    backoff_delays = [2, 4, 8]

    async def _attempt(temp: float) -> Optional[Any]:
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=settings.MODEL_NAME,
                    messages=messages,
                    temperature=temp,
                    response_format={"type": "json_object"},
                    max_tokens=4096,
                ),
                timeout=timeout,
            )
            content = response.choices[0].message.content
            data = json.loads(content)
            return response_schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"Groq response parse/validation error: {e}")
            return None
        except asyncio.TimeoutError:
            logger.warning("Groq call timed out")
            return None

    # First attempt
    result = await _attempt(temperature)
    if result is not None:
        return result

    # Validation failure retry with temperature=0
    logger.info("Retrying Groq call with temperature=0...")
    for attempt, delay in enumerate(backoff_delays):
        try:
            result = await _attempt(0.0)
            if result is not None:
                return result
        except groq.RateLimitError:
            logger.warning(f"Rate limited, waiting {delay}s (attempt {attempt+1}/3)...")
            await asyncio.sleep(delay)
        except (groq.APIConnectionError, groq.APIStatusError) as e:
            logger.error(f"Groq API error: {e}")
            break
        except Exception as e:
            logger.error(f"Unexpected Groq error: {e}")
            break

    logger.error("All Groq retry attempts exhausted, returning None")
    return None
