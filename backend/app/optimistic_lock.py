"""``If-Match`` header parsing for optimistic-lock endpoints (task 180).

Tables with a ``version_int`` column (AnnualPackage, AssessmentSetup,
AppendixDocument, hoa_settings) need clients to send the version they
read on every state-changing call. The server rejects the call with
HTTP 412 (Precondition Failed) if the version doesn't match — the
client must re-fetch and try again.

The header format is the standard RFC 7232 ``If-Match: "<version>"``
(quotes optional in practice; we strip them). Missing header triggers
HTTP 428 (Precondition Required) for endpoints that opt in via the
``require_if_match`` dependency. Endpoints that accept a body-supplied
``expected_version`` for backward compat (e.g. the existing appendix
update path) can keep using that — :func:`parse_if_match` is the
canonical header path for new endpoints.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException


def _strip_quotes(raw: str) -> str:
    stripped = raw.strip()
    if (stripped.startswith('"') and stripped.endswith('"')) or (
        stripped.startswith("'") and stripped.endswith("'")
    ):
        return stripped[1:-1]
    return stripped


def parse_if_match(value: str) -> int:
    """Parse a raw ``If-Match`` header value into an integer version.

    Raises HTTP 412 on malformed values so the client can distinguish
    "missing precondition" (428) from "precondition syntactically
    invalid" (412).
    """
    candidate = _strip_quotes(value)
    if not candidate:
        raise HTTPException(
            status_code=412,
            detail="If-Match header is empty",
        )
    try:
        return int(candidate)
    except ValueError as exc:
        raise HTTPException(
            status_code=412,
            detail=f"If-Match header is not an integer: {value!r}",
        ) from exc


def require_if_match(
    if_match: Optional[str] = Header(
        default=None, alias="If-Match",
        description="Optimistic-lock version (integer). REQUIRED for "
                    "state-changing endpoints on rows with version_int.",
    ),
) -> int:
    """FastAPI dependency: parse + require the ``If-Match`` header.

    Raises HTTP 428 (Precondition Required) when missing — clients
    must always send it for endpoints that opt in.
    """
    if if_match is None:
        raise HTTPException(
            status_code=428,
            detail="If-Match header is required for this endpoint",
        )
    return parse_if_match(if_match)


__all__ = [
    "parse_if_match",
    "require_if_match",
]
