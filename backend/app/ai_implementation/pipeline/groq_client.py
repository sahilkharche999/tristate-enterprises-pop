"""AsyncGroq client wrapper with retry logic and graceful fallback."""
import asyncio
import json
import logging
import re
from typing import Optional, Any
import groq
from groq import AsyncGroq
from pydantic import BaseModel, ValidationError

from ..config import settings

logger = logging.getLogger(__name__)

# Module-level singleton: reuse the same client (and its connection pool) across calls.
_groq_client: Optional[AsyncGroq] = None

_METRIC_FIELD_MAP = {
    "actual": "current_actual",
    "current actual": "current_actual",
    "current period actual": "current_actual",
    "budget": "current_budget",
    "current budget": "current_budget",
    "current period budget": "current_budget",
    "variance": "current_variance",
    "current variance": "current_variance",
    "current period variance": "current_variance",
    "year to date actual": "ytd_actual",
    "year-to-date actual": "ytd_actual",
    "ytd actual": "ytd_actual",
    "year to date budget": "ytd_budget",
    "year-to-date budget": "ytd_budget",
    "ytd budget": "ytd_budget",
    "year to date variance": "ytd_variance",
    "year-to-date variance": "ytd_variance",
    "ytd variance": "ytd_variance",
    "annual budget": "annual_budget",
}


def _looks_like_financial_statement_schema(response_schema: type[BaseModel]) -> bool:
    field_names = set(getattr(response_schema, "model_fields", {}).keys())
    return {"document_family", "report_type", "line_items"}.issubset(field_names)


def _normalize_metric_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _parse_numeric_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    negative = "(" in text and ")" in text
    cleaned = text.replace("$", "").replace(",", "").replace("(", "").replace(")", "").strip()
    if not cleaned:
        return None

    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -number if negative else number


def _is_metric_map(node: Any) -> bool:
    if not isinstance(node, dict) or not node:
        return False

    normalized_keys = {_normalize_metric_key(str(key)) for key in node.keys()}
    matched = normalized_keys & set(_METRIC_FIELD_MAP.keys())
    return len(matched) >= 2


def _infer_section_kind(path: list[str]) -> Optional[str]:
    joined = " ".join(path).lower()
    if "reserve" in joined and "expense" in joined:
        return "reserve"
    if "expense" in joined:
        return "operating"
    if "income" in joined or "revenue" in joined:
        return "income"
    return None


def _split_account_label(name: str) -> tuple[Optional[str], str]:
    match = re.match(r"^\s*([0-9][0-9A-Za-z.\-]*)\s*(?:-\s*)?(.+?)\s*$", name)
    if not match:
        return None, name.strip()
    return match.group(1).strip(), match.group(2).strip()


def _normalize_tree_shaped_financial_statement(data: Any) -> Optional[dict[str, Any]]:
    if not isinstance(data, dict) or not data:
        return None

    line_items: list[dict[str, Any]] = []
    root_key = next(iter(data.keys()))
    root_value = data[root_key]
    report_type = "income_statement" if "income statement" in str(root_key).lower() else "financial_statement"

    def walk(node: Any, path: list[str]) -> None:
        if not isinstance(node, dict):
            return

        if _is_metric_map(node):
            label_key = path[-1] if path else "Line Item"
            if label_key.lower().startswith("total "):
                return

            account_code_text, label = _split_account_label(label_key)
            item: dict[str, Any] = {
                "account_code_text": account_code_text,
                "label": label,
                "section_label": path[1] if len(path) > 1 else None,
                "section_kind": _infer_section_kind(path),
                "evidence": {"source_path": path},
            }
            for raw_key, raw_value in node.items():
                mapped_field = _METRIC_FIELD_MAP.get(_normalize_metric_key(str(raw_key)))
                if mapped_field is None:
                    continue
                item[mapped_field] = _parse_numeric_value(raw_value)
            line_items.append(item)
            return

        for child_key, child_value in node.items():
            walk(child_value, [*path, str(child_key)])

    walk(root_value, [str(root_key)])
    if not line_items:
        return None

    return {
        "document_family": "pdf_visual_document",
        "report_type": report_type,
        "statement_period": None,
        "line_items": line_items,
        "totals": [],
        "validation_issues": [],
        "confidence": 0.6,
    }


def _scrub_null_labels(data: dict[str, Any]) -> dict[str, Any]:
    """Drop line items where the LLM returned null/empty label."""
    items = data.get("line_items")
    if not isinstance(items, list):
        return data
    cleaned = [i for i in items if isinstance(i, dict) and isinstance(i.get("label"), str) and i["label"].strip()]
    if len(cleaned) < len(items):
        logger.info("Dropped %d items with null/empty labels", len(items) - len(cleaned))
    return {**data, "line_items": cleaned}


def _validate_or_repair_response(
    data: dict[str, Any],
    response_schema: type[BaseModel],
) -> Optional[Any]:
    data = _scrub_null_labels(data)
    try:
        return response_schema.model_validate(data)
    except ValidationError as exc:
        if not _looks_like_financial_statement_schema(response_schema):
            logger.warning(f"Groq vision response parse/validation error: {exc}")
            return None

        normalized = _normalize_tree_shaped_financial_statement(data)
        if normalized is None:
            logger.warning(f"Groq vision response parse/validation error: {exc}")
            return None

        try:
            return response_schema.model_validate(normalized)
        except ValidationError as repaired_exc:
            logger.warning(f"Groq vision response parse/validation error: {repaired_exc}")
            return None


def get_groq_client() -> AsyncGroq:
    """Return the module-level AsyncGroq client, creating it on first use."""
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    return _groq_client


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

    # All attempts use the same error handling: rate-limit → backoff, connection error → abort
    temps = [temperature, 0.0, 0.0, 0.0]  # first at requested temp, retries at 0
    for attempt, (temp, delay) in enumerate(zip(temps, [0] + backoff_delays)):
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            result = await _attempt(temp)
            if result is not None:
                return result
            if attempt == 0:
                logger.info("Retrying Groq call with temperature=0...")
        except groq.RateLimitError:
            logger.warning(f"Rate limited, waiting {backoff_delays[min(attempt, len(backoff_delays)-1)]}s (attempt {attempt+1}/{len(temps)})...")
        except (groq.APIConnectionError, groq.APIStatusError) as e:
            logger.error(f"Groq API error: {e}")
            break
        except Exception as e:
            logger.error(f"Unexpected Groq error: {e}")
            break

    logger.error("All Groq retry attempts exhausted, returning None")
    return None


def _repair_truncated_json(raw: str) -> Optional[dict]:
    """Attempt to recover a valid JSON object from a truncated response.

    When the model hits max_tokens mid-output (finish_reason=length), the JSON
    is cut off. This function tries to close open arrays/objects to salvage
    whatever line items were fully serialized before the cutoff.
    """
    text = raw.strip()
    if not text:
        return None

    # Try parsing as-is first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Close open structures from the end
    # Remove any trailing partial value (cut mid-string or mid-number)
    # Find last complete JSON element boundary
    last_complete = max(
        text.rfind("}"),
        text.rfind("]"),
        text.rfind(","),
    )
    if last_complete <= 0:
        return None

    truncated = text[:last_complete + 1].rstrip(",")

    # Count unclosed brackets/braces and close them
    open_braces = truncated.count("{") - truncated.count("}")
    open_brackets = truncated.count("[") - truncated.count("]")

    truncated += "]" * max(open_brackets, 0)
    truncated += "}" * max(open_braces, 0)

    try:
        return json.loads(truncated)
    except json.JSONDecodeError:
        pass

    # More aggressive: find the last complete line_items entry
    last_closing_brace = truncated.rfind("}")
    if last_closing_brace > 0:
        aggressive = truncated[:last_closing_brace + 1]
        open_braces = aggressive.count("{") - aggressive.count("}")
        open_brackets = aggressive.count("[") - aggressive.count("]")
        aggressive += "]" * max(open_brackets, 0)
        aggressive += "}" * max(open_braces, 0)
        try:
            return json.loads(aggressive)
        except json.JSONDecodeError:
            pass

    return None


async def call_groq_vision(
    messages: list[dict],
    response_schema: type[BaseModel],
    temperature: float = 0.0,
    timeout: float = 60.0,
) -> Optional[Any]:
    """Call a Groq vision-capable model and return parsed structured output.

    Uses json_object mode (not json_schema) to avoid the model producing
    minimal schema-valid answers. Handles truncated responses when the model
    hits max_tokens by repairing the JSON and salvaging completed line items.
    """
    client = get_groq_client()
    backoff_delays = [2, 4, 8]

    def _normalize_messages(payload: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        for message in payload:
            content = message.get("content")
            if not isinstance(content, list):
                normalized.append(message)
                continue

            normalized_parts: list[dict] = []
            for part in content:
                if part.get("type") != "image_url":
                    normalized_parts.append(part)
                    continue

                image_url = part.get("image_url") or {}
                url = image_url.get("url")
                if isinstance(url, str) and url.startswith("data:image/"):
                    normalized_parts.append({"type": "image_url", "image_url": {"url": url}})
                else:
                    normalized_parts.append(part)

            normalized.append({**message, "content": normalized_parts})
        return normalized

    async def _attempt(temp: float) -> Optional[Any]:
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=settings.DOCUMENT_VLM_MODEL,
                    messages=_normalize_messages(messages),
                    temperature=temp,
                    response_format={"type": "json_object"},
                    max_tokens=8192,
                ),
                timeout=timeout,
            )
            raw_content = response.choices[0].message.content
            finish_reason = getattr(response.choices[0], "finish_reason", None)
            usage = getattr(response, "usage", None)
            completion_tokens = getattr(usage, "completion_tokens", None) if usage else None

            logger.info(
                "Groq vision response: finish_reason=%s, completion_tokens=%s, content_len=%d",
                finish_reason,
                completion_tokens,
                len(raw_content) if raw_content else 0,
            )

            if not raw_content:
                return None

            # If truncated (hit token limit), try to repair the JSON
            if finish_reason == "length":
                logger.warning(
                    "Groq vision response truncated at %s tokens, attempting JSON repair",
                    completion_tokens,
                )
                data = _repair_truncated_json(raw_content)
                if data is None:
                    logger.error("JSON repair failed on truncated response")
                    return None
                items_count = len(data.get("line_items", []))
                logger.info("JSON repair recovered %d line items from truncated response", items_count)
            else:
                try:
                    data = json.loads(raw_content)
                except json.JSONDecodeError as e:
                    logger.warning(f"Groq vision response parse error: {e}")
                    return None

            return _validate_or_repair_response(data, response_schema)
        except asyncio.TimeoutError:
            logger.warning("Groq vision call timed out")
            return None

    temps = [temperature, 0.0, 0.0, 0.0]
    for attempt, (temp, delay) in enumerate(zip(temps, [0] + backoff_delays)):
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            result = await _attempt(temp)
            if result is not None:
                return result
            if attempt == 0:
                logger.info("Retrying Groq vision call with temperature=0...")
        except groq.RateLimitError:
            logger.warning(
                f"Vision request rate limited, waiting {backoff_delays[min(attempt, len(backoff_delays)-1)]}s "
                f"(attempt {attempt+1}/{len(temps)})..."
            )
        except (groq.APIConnectionError, groq.APIStatusError) as e:
            logger.error(f"Groq vision API error: {e}")
            break
        except Exception as e:
            logger.error(f"Unexpected Groq vision error: {e}")
            break

    logger.error("All Groq vision retry attempts exhausted, returning None")
    return None
