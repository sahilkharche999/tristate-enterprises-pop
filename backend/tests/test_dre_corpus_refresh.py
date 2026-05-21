"""Periodic-refresh diff helper tests (Phase 3 task 92)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.dre_corpus_refresh import (
    diff_extraction_against_fixture,
    fixture_audit_envelope,
)


def test_diff_returns_empty_for_identical_payloads(tmp_path):
    fixture = tmp_path / "expected.json"
    fixture.write_text(json.dumps({
        "_meta": {"operator_verified_by": "ops"},
        "assessment_setup": {"setup_type": "fixed"},
        "allocation_pools": [{"pool_key": "operating"}],
    }))
    diffs = diff_extraction_against_fixture(
        fixture_name="test",
        expected_json_path=fixture,
        actual_extraction_json={
            "assessment_setup": {"setup_type": "fixed"},
            "allocation_pools": [{"pool_key": "operating"}],
        },
    )
    assert diffs == []


def test_diff_surfaces_leaf_changes(tmp_path):
    fixture = tmp_path / "expected.json"
    fixture.write_text(json.dumps({
        "_meta": {"operator_verified_by": "ops"},
        "assessment_setup": {"setup_type": "fixed", "confidence": 0.9},
    }))
    diffs = diff_extraction_against_fixture(
        fixture_name="test",
        expected_json_path=fixture,
        actual_extraction_json={
            "assessment_setup": {"setup_type": "grouped", "confidence": 0.85},
        },
    )
    paths = [d.field_path for d in diffs]
    assert "assessment_setup.setup_type" in paths
    assert "assessment_setup.confidence" in paths


def test_diff_surfaces_list_drift(tmp_path):
    fixture = tmp_path / "expected.json"
    fixture.write_text(json.dumps({
        "_meta": {"operator_verified_by": "ops"},
        "allocation_pools": [{"pool_key": "operating"}, {"pool_key": "reserve"}],
    }))
    diffs = diff_extraction_against_fixture(
        fixture_name="test",
        expected_json_path=fixture,
        actual_extraction_json={
            "allocation_pools": [{"pool_key": "operating"}],  # missing reserve
        },
    )
    assert any("allocation_pools[1]" in d.field_path for d in diffs)


def test_audit_envelope_reads_meta(tmp_path):
    fixture = tmp_path / "expected.json"
    fixture.write_text(json.dumps({
        "_meta": {
            "operator_verified_by": "ops@example.com",
            "operator_verified_at": "2026-01-01",
            "operator_verified_prompt_sha256": "abc123",
        },
        "assessment_setup": {},
    }))
    env = fixture_audit_envelope(fixture)
    assert env is not None
    assert env["operator_verified_by"] == "ops@example.com"


def test_audit_envelope_returns_none_when_missing(tmp_path):
    fixture = tmp_path / "expected.json"
    fixture.write_text(json.dumps({"assessment_setup": {}}))
    assert fixture_audit_envelope(fixture) is None
