"""Per-render formula audit log (CONTEXT D-05, D-07, D-15).

Each `@audit_formula`-decorated function records its call in the active
audit context. Outside an audit context, the decorator is a no-op so
formulas remain pure-functional and unit-testable from `pytest`.

Per RESEARCH § "Open Questions" Q8: ONE entry per top-level call. Inner
sum() / nested decorated calls are an implementation detail, not an
audit event. The re-entrancy guard below enforces this.

Threat T-11-04 mitigation: every recorded entry carries `formula_id +
version + inputs + output + computed_at`. Decimals are serialized as
strings so `model_dump_json()` / `model_validate_json()` round-trip
exactly — a tampered regeneration of the same inputs produces a
different audit log (T-11-04 detection vector).
"""
from __future__ import annotations

import functools
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterator, Optional

from .schemas import AuditLog, FormulaCall


_local = threading.local()


def _current_log() -> Optional[AuditLog]:
    return getattr(_local, "log", None)


def _serialize(value: Any) -> Any:
    """Stringify Decimals; recurse through dict/list/tuple; pass through JSON-safe types.

    Pydantic models (FormulaCall, AuditLog, etc.) become dicts via `model_dump`.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if hasattr(value, "model_dump"):
        # Pydantic v2 BaseModel
        return value.model_dump()
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def audit_formula(*, name: str, version: int) -> Callable:
    """Decorator that records each top-level call to a formula function.

    Args:
        name: stable formula_id (e.g. "percent_funded"). Used as primary key
              in the audit log.
        version: integer formula version. Bumped when the formula changes;
              used by Phase 12+ to detect tampering and by manual overrides
              to distinguish "system computed at v=N" from "human edited".
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            log = _current_log()

            # Re-entrancy guard: only the OUTERMOST decorated call is recorded.
            # If a decorated function calls another decorated function the
            # nested call still executes correctly but is NOT logged. This
            # implements RESEARCH OQ-8 ("1 entry per top-level call").
            already_in_call = getattr(_local, "in_call", False)
            if log is not None and not already_in_call:
                _local.in_call = True
                try:
                    output = fn(*args, **kwargs)
                finally:
                    _local.in_call = False
                inputs_payload: dict[str, Any] = {
                    k: _serialize(v) for k, v in kwargs.items()
                }
                # Positional args are recorded under arg_0, arg_1, … (most
                # formulas use kwargs only; this is a fallback).
                for i, v in enumerate(args):
                    inputs_payload[f"arg_{i}"] = _serialize(v)
                log.formula_calls.append(
                    FormulaCall(
                        formula_id=name,
                        version=version,
                        inputs=inputs_payload,
                        output=_serialize(output),
                        computed_at=_now_iso(),
                    )
                )
                return output

            # No audit context active OR we are nested inside another
            # decorated call → execute transparently without recording.
            return fn(*args, **kwargs)

        wrapper.__audit_name__ = name  # type: ignore[attr-defined]
        wrapper.__audit_version__ = version  # type: ignore[attr-defined]
        return wrapper

    return decorator


@contextmanager
def audit_context(input_snapshot: dict[str, Any]) -> Iterator[AuditLog]:
    """Open an audit log for a single render.

    The render pipeline is expected to call this once per `POST /generate`
    request, run the calc DAG inside the `with` block, then serialize the
    yielded `AuditLog` to disk alongside the output PDF (CONTEXT D-15).
    """
    log = AuditLog(
        input_snapshot=_serialize(input_snapshot),
        formula_calls=[],
        started_at=_now_iso(),
    )
    previous = getattr(_local, "log", None)
    _local.log = log
    try:
        yield log
    finally:
        log.completed_at = _now_iso()
        _local.log = previous
