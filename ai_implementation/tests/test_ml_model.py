"""Tests for ml_model background training."""
import asyncio
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ai_implementation.pipeline.ml_model import train_async


def test_train_async_has_no_db_parameter():
    """train_async must not accept a db parameter — it must create its own connection
    to avoid using a request-scoped connection that may be closed before training ends."""
    sig = inspect.signature(train_async)
    assert "db" not in sig.parameters, (
        "train_async must not accept a db parameter; it must open its own connection "
        "from settings.DB_PATH."
    )
