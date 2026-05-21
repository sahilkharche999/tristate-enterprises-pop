"""Fixture loader tests (Phase 3.7).

The loader's job is to refuse silent AI-only ground truth: any fixture
without an operator name in ``_meta.operator_verified_by`` fails to load.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.fixture_loader import (
    FixtureMetadataError,
    load_fixture,
)


def _write_fixture(tmp_path: Path, name: str, meta: dict, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({"_meta": meta, "data": data}))
    return p


class TestFixtureLoader:
    def test_loads_well_formed_fixture(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "tests.fixtures.fixture_loader.FIXTURE_ROOT", tmp_path
        )
        _write_fixture(
            tmp_path, "ok.json",
            {
                "source_document": "DRE/sample.pdf",
                "source_pages": [14, 15],
                "operator_verified_by": "ops@example.com",
                "verified_at": "2026-05-17T10:00:00Z",
                "notes": "smoke",
            },
            {"value": 42},
        )
        loaded = load_fixture("ok.json")
        assert loaded.data == {"value": 42}
        assert loaded.meta.operator_verified_by == "ops@example.com"
        assert loaded.meta.source_pages == [14, 15]

    def test_missing_operator_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "tests.fixtures.fixture_loader.FIXTURE_ROOT", tmp_path
        )
        _write_fixture(
            tmp_path, "no_op.json",
            {
                "source_document": "DRE/sample.pdf",
                "source_pages": [14],
                "operator_verified_by": "",  # empty
                "verified_at": "2026-05-17T10:00:00Z",
            },
            {"value": 1},
        )
        with pytest.raises(FixtureMetadataError, match="operator_verified_by"):
            load_fixture("no_op.json")

    def test_whitespace_only_operator_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "tests.fixtures.fixture_loader.FIXTURE_ROOT", tmp_path
        )
        _write_fixture(
            tmp_path, "ws.json",
            {
                "source_document": "x",
                "source_pages": [],
                "operator_verified_by": "   ",
                "verified_at": "",
            },
            {},
        )
        with pytest.raises(FixtureMetadataError):
            load_fixture("ws.json")

    def test_missing_meta_key_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "tests.fixtures.fixture_loader.FIXTURE_ROOT", tmp_path
        )
        p = tmp_path / "bare.json"
        p.write_text(json.dumps({"value": 1}))  # no _meta or data wrapper
        with pytest.raises(FixtureMetadataError, match="_meta"):
            load_fixture("bare.json")

    def test_missing_file_raises_filenotfound(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "tests.fixtures.fixture_loader.FIXTURE_ROOT", tmp_path
        )
        with pytest.raises(FileNotFoundError):
            load_fixture("does_not_exist.json")
