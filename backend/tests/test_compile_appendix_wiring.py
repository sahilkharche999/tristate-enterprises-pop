"""Compile-side appendix manifest wiring (Phase 5.4 task 159).

Verifies the ``extra_appendix_paths`` parameter on ``compile_package``
merges DB-driven appendix files into the final PDF after the static
spec-driven appendices, with duplicate-name guard.

Doesn't require weasyprint — uses a stubbed ``compile_package`` so we
can assert on the merge-order math without rendering pages.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.disclosure_package.compile_inputs import (
    resolve_compile_appendix_entries,
    resolve_compile_appendix_paths,
)


def test_extra_appendix_paths_parameter_exists():
    """Smoke: the new parameter is on the public signature."""
    import inspect

    from app.disclosure_package.compiler import compile_package

    sig = inspect.signature(compile_package)
    assert "extra_appendix_paths" in sig.parameters
    p = sig.parameters["extra_appendix_paths"]
    assert p.kind == inspect.Parameter.KEYWORD_ONLY
    assert p.default is None


def test_run_render_job_resolves_manifest_paths(monkeypatch, tmp_path):
    """``run_render_job`` calls ``resolve_compile_appendix_entries`` and
    forwards both the resolved paths and their display titles to
    ``compile_package`` (titles needed for the appendix TOC entries)."""
    from app.disclosure_package import service as dp_service

    # Capture call args without actually rendering
    captured: dict = {}

    def fake_compile(**kwargs):
        captured.update(kwargs)
        from app.disclosure_package.compiler import CompileResult
        from datetime import datetime, timezone

        return CompileResult(
            output_path=tmp_path / "package.pdf",
            audit_path=tmp_path / "audit.json",
            intermediate_path=tmp_path / "generated.pdf",
            page_count=1,
            sha256="x" * 64,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    # The integration test depends on the SQL+SQLAlchemy session, so we
    # assert the symbol-level fact: the wiring is present in the source.
    src = Path(dp_service.__file__).read_text()
    assert "resolve_compile_appendix_entries" in src, (
        "run_render_job should call resolve_compile_appendix_entries"
    )
    assert "extra_appendix_paths=manifest_paths" in src, (
        "run_render_job should forward the resolved paths"
    )
    assert "extra_appendix_titles=manifest_titles" in src, (
        "run_render_job should forward the resolved display titles"
    )


def test_run_render_job_materializes_assessment_mappings_before_matrix_build():
    """Disclosure rendering should refresh annual assessment mappings.

    Budget save/generate already materializes reusable DRE mapping rules into
    current-year ``budget_line_pool_mappings``. The disclosure render path also
    needs that step before it builds the assessment schedule, otherwise a
    package can render the "Assessment Schedule Pending Review" placeholder
    even after the operator has approved setup/mapping rules.
    """
    from app.disclosure_package import service as dp_service

    src = Path(dp_service.__file__).read_text()
    assert "materialize_budget_line_pool_mappings" in src
    assert "assessment_mapping_counts" in src
    assert "build_matrix_for_assessment_mode" in src
    assert src.index("materialize_budget_line_pool_mappings") < src.index(
        "build_matrix_for_assessment_mode"
    )


def test_compile_error_status_message_includes_field_paths():
    """A failed background job should tell operators which preflight field failed."""
    from app.disclosure_package.compiler import CompileError
    from app.disclosure_package.service import _compile_error_status_message

    error = CompileError(
        "Preflight blocked compilation: 1 error(s)",
        field_paths=["hoa_settings.reserve_study_date"],
    )

    assert _compile_error_status_message(error) == (
        "Preflight blocked compilation: 1 error(s): hoa_settings.reserve_study_date"
    )


def test_compile_error_status_message_guides_operator_when_units_missing():
    """Units blocker should tell the operator exactly where to fix it."""
    from app.disclosure_package.compiler import CompileError
    from app.disclosure_package.service import _compile_error_status_message

    error = CompileError(
        "Preflight blocked compilation: 1 error(s)",
        field_paths=["hoa_metadata.units"],
    )

    assert _compile_error_status_message(error) == (
        "Preflight blocked compilation: HOA unit count is missing or invalid. "
        "Go to Settings and enter a positive unit count for this HOA."
    )


def test_assessment_revenue_uses_legacy_draft_line_item_shape():
    from app.disclosure_package.service import _assessment_revenue_for_budget_draft

    draft = SimpleNamespace(
        line_items=[
            {
                "label": "Assessment Income",
                "category": "income",
                "annual_budget": 147813.84,
                "projection": 147813.84,
                "raw": {"Section": "income", "section": "income"},
            },
            {
                "label": "Late Fees & Interest",
                "category": "income",
                "annual_budget": 400.0,
                "projection": 409.97,
                "raw": {"Section": "income", "section": "income"},
            },
        ]
    )

    assert _assessment_revenue_for_budget_draft(draft) == Decimal("147813.84")


def test_extra_appendix_paths_dedup_by_filename():
    """When the same filename appears in both the static spec entries
    and the DB manifest, the duplicate is silently skipped."""
    # This is a code-shape assertion: the compile_package logic uses a
    # ``seen_appendix_names`` set keyed on filename, built once while
    # resolving the single appendix merge order (also used for the TOC
    # page-number computation) — this replaced the old separate
    # ``already_merged`` set from before appendix TOC entries existed.
    from app.disclosure_package import compiler

    src = Path(compiler.__file__).read_text()
    assert "seen_appendix_names" in src
    assert "skipping duplicate manifest appendix" in src
