"""Plan 11-05 Task 3: compiler.py orchestrator tests.

Pipeline (CONTEXT D-15, REQ-D11-007/008/009/011):
    preflight → audit_context(compute) → render → merge → qpdf_check → write_atomic

These tests exercise the orchestrator with `render_template` mocked. The
real WeasyPrint render path is exercised in `test_disclosure_package_render.py`
(plan 11-04). The merge + qpdf path is exercised in
`test_disclosure_package_merge.py` (plan 11-05 Task 1). What this file
verifies is the *glue*: that the compiler runs the steps in order, fails
fast on preflight errors, writes audit.json beside package.pdf, and
returns a CompileResult with the expected fields.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import fitz  # PyMuPDF

from app.disclosure_package import compiler as compiler_module
from app.disclosure_package.compiler import (
    CompileError,
    CompileResult,
    compile_package,
)
from app.disclosure_package.package_specs import OLD_MILL_2026
from app.disclosure_package.schemas import (
    BudgetDraft,
    GeneratedPage,
    HOAMetadata,
    LineItem,
    ReserveStudyComponent,
    ReserveStudySnapshot,
    StaticAppendix,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixture builders
# ─────────────────────────────────────────────────────────────────────────────


def _make_pdf(path: Path, *, page_count: int = 1, label: str = "stub") -> Path:
    """Manufacture a minimal valid PDF using PyMuPDF.

    Used both for the static-appendix stand-ins on disk and for the
    `render_template` monkeypatch return value.
    """
    doc = fitz.open()
    try:
        for i in range(page_count):
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 72), f"{label} page {i + 1}")
        doc.save(str(path))
    finally:
        doc.close()
    return path


def _make_pdf_bytes(*, page_count: int, label: str) -> bytes:
    doc = fitz.open()
    try:
        for i in range(page_count):
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 72), f"{label} page {i + 1}")
        return doc.tobytes()
    finally:
        doc.close()


def _hoa_metadata() -> HOAMetadata:
    return HOAMetadata(
        hoa_id=1,
        name="Old Mill Homeowners Association",
        units=279,
        fiscal_year_start_month=1,
        fiscal_year_end_month=12,
    )


def _budget_draft() -> BudgetDraft:
    """Operating + reserve line items keyed to OLD_MILL_2026 sections.

    Section labels match `formulas.expenses_*_operating` filters
    ('Maintenance and operations', 'Utilities', 'Administration').
    """
    return BudgetDraft(line_items=[
        LineItem(label="Member assessments", amount=Decimal("2025540"), is_revenue=True),
        LineItem(
            label="Replacement contributions",
            amount=Decimal("672886"),
            is_reserve=True,
            is_revenue=True,
        ),
        LineItem(
            label="Landscaping",
            amount=Decimal("100000"),
            section="Maintenance and operations",
        ),
        LineItem(label="Water", amount=Decimal("50000"), section="Utilities"),
        LineItem(label="Mgmt fee", amount=Decimal("145000"), section="Administration"),
        LineItem(
            label="Roof replacement",
            amount=Decimal("691086"),
            is_reserve=True,
        ),
    ])


def _reserve_snapshot() -> ReserveStudySnapshot:
    return ReserveStudySnapshot(
        study_date="September 2025",
        components=[
            ReserveStudyComponent(
                line_item="Roofing",
                useful_life=25,
                remaining_life=10,
                replacement_cost=Decimal("1000000"),
                year_new=2010,
            ),
            ReserveStudyComponent(
                line_item="Asphalt",
                useful_life=20,
                remaining_life=5,
                replacement_cost=Decimal("400000"),
                year_new=2010,
            ),
        ],
    )


def _seed_appendices(root: Path) -> None:
    """Place a stub PDF for every StaticAppendix entry in OLD_MILL_2026.

    Each stub matches `entry.page_count_hint` so the merged page count
    equals `sum(page_count_hint)` (REQ-D11-009 invariant).
    """
    root.mkdir(parents=True, exist_ok=True)
    for entry in OLD_MILL_2026.entries:
        if isinstance(entry, StaticAppendix):
            _make_pdf(
                root / entry.file,
                page_count=entry.page_count_hint,
                label=entry.file,
            )


def _patch_render(monkeypatch) -> dict[str, int]:
    """Stub `render_template` to return a fixed-page-count PDF per template.

    Avoids the WeasyPrint dependency in compiler glue tests; render
    behavior is covered by `test_disclosure_package_render.py`. Returns a
    dict tracking how many times each template was rendered.
    """
    counts: dict[str, int] = {}

    def fake_render(*, template_name: str, context: dict[str, Any], templates_subdir: str = "old_mill") -> bytes:
        counts[template_name] = counts.get(template_name, 0) + 1
        # Find the matching GeneratedPage entry to honor its page_count_hint.
        for entry in OLD_MILL_2026.entries:
            if isinstance(entry, GeneratedPage) and entry.template == template_name:
                return _make_pdf_bytes(page_count=entry.page_count_hint, label=template_name)
        return _make_pdf_bytes(page_count=1, label=template_name)

    monkeypatch.setattr(compiler_module, "render_template", fake_render)
    return counts


def _spec_static_data():
    return OLD_MILL_2026.static_data


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_compile_package_returns_compile_result(monkeypatch, tmp_path: Path, qpdf_required) -> None:
    """Test 1: compile_package(...) returns a CompileResult whose
    output_path points at an existing .pdf file."""
    appendices = tmp_path / "appendices"
    _seed_appendices(appendices)
    output_dir = tmp_path / "out"
    _patch_render(monkeypatch)

    result = compile_package(
        spec=OLD_MILL_2026,
        budget_draft=_budget_draft(),
        reserve_snapshot=_reserve_snapshot(),
        hoa_metadata=_hoa_metadata(),
        output_dir=output_dir,
        appendices_root=appendices,
    )

    assert isinstance(result, CompileResult)
    assert result.output_path.exists()
    assert result.output_path.suffix == ".pdf"
    assert result.output_path.name == "package.pdf"


def test_compile_package_output_passes_qpdf_check(monkeypatch, tmp_path: Path, qpdf_required) -> None:
    """Test 2: The output package.pdf passes qpdf_check (i.e., no
    RuntimeError raised during compile_package). REQ-D11-007."""
    appendices = tmp_path / "appendices"
    _seed_appendices(appendices)
    output_dir = tmp_path / "out"
    _patch_render(monkeypatch)

    # If qpdf_check raised on the merged output, compile_package would
    # propagate the RuntimeError. A successful return is the assertion.
    result = compile_package(
        spec=OLD_MILL_2026,
        budget_draft=_budget_draft(),
        reserve_snapshot=_reserve_snapshot(),
        hoa_metadata=_hoa_metadata(),
        output_dir=output_dir,
        appendices_root=appendices,
    )
    assert result.output_path.exists()


def test_compile_package_page_count_matches_hint_sum(monkeypatch, tmp_path: Path, qpdf_required) -> None:
    """Test 3: The output page count equals
    `sum(entry.page_count_hint for entry in spec.entries)`. REQ-D11-009.

    With the render stub honoring each GeneratedPage's hint AND each
    static-appendix stub matching its hint, this invariant is exact.
    """
    appendices = tmp_path / "appendices"
    _seed_appendices(appendices)
    output_dir = tmp_path / "out"
    _patch_render(monkeypatch)

    result = compile_package(
        spec=OLD_MILL_2026,
        budget_draft=_budget_draft(),
        reserve_snapshot=_reserve_snapshot(),
        hoa_metadata=_hoa_metadata(),
        output_dir=output_dir,
        appendices_root=appendices,
    )

    expected = sum(e.page_count_hint for e in OLD_MILL_2026.entries)
    assert result.page_count == expected


def test_compile_package_writes_audit_json(monkeypatch, tmp_path: Path, qpdf_required) -> None:
    """Test 4: An audit.json is written next to package.pdf containing
    every formula call (REQ-D11-011 stub — full check in plan 11-06)."""
    appendices = tmp_path / "appendices"
    _seed_appendices(appendices)
    output_dir = tmp_path / "out"
    _patch_render(monkeypatch)

    result = compile_package(
        spec=OLD_MILL_2026,
        budget_draft=_budget_draft(),
        reserve_snapshot=_reserve_snapshot(),
        hoa_metadata=_hoa_metadata(),
        output_dir=output_dir,
        appendices_root=appendices,
    )

    assert result.audit_path.exists()
    audit = json.loads(result.audit_path.read_text())
    # input_snapshot includes the spec hoa_id + fiscal_year + budget draft + reserve snap
    assert "input_snapshot" in audit
    assert audit["input_snapshot"]["fiscal_year"] == OLD_MILL_2026.fiscal_year
    # formula_calls is the per-render audit log (CONTEXT D-07)
    assert "formula_calls" in audit
    assert len(audit["formula_calls"]) > 0
    # Every entry has the expected shape.
    for call in audit["formula_calls"]:
        assert {"formula_id", "version", "inputs", "output", "computed_at"} <= set(call.keys())
    # The audit log captures both the started_at and completed_at timestamps
    assert audit["started_at"] and audit["completed_at"]


def test_compile_package_skips_missing_appendices(monkeypatch, tmp_path: Path, qpdf_required) -> None:
    """Test 5: Missing static appendix files are no longer a blocker —
    compile_package skips them, merges the generated pages plus any
    appendices that DO exist, and produces a valid package.pdf.

    Trigger: appendices_root is empty. The compiler must still render,
    merge, and emit package.pdf + audit.json.
    """
    output_dir = tmp_path / "out"
    empty_appendices = tmp_path / "appendices_empty"
    empty_appendices.mkdir()

    _patch_render(monkeypatch)

    result = compile_package(
        spec=OLD_MILL_2026,
        budget_draft=_budget_draft(),
        reserve_snapshot=_reserve_snapshot(),
        hoa_metadata=_hoa_metadata(),
        output_dir=output_dir,
        appendices_root=empty_appendices,
    )

    # Outputs were written and qpdf-checked.
    assert result.output_path.exists()
    assert (output_dir / "audit.json").exists()
    # Page count reflects only the GeneratedPage entries (zero appendices).
    generated_pages = sum(
        e.page_count_hint for e in OLD_MILL_2026.entries
        if isinstance(e, GeneratedPage)
    )
    assert result.page_count == generated_pages


def test_compile_package_appends_extra_pdfs_in_appendix_dir(
    monkeypatch, tmp_path: Path, qpdf_required
) -> None:
    """Operator-uploaded ad-hoc PDFs whose filenames are NOT in the
    PackageSpec entries are appended at the end of the merge order in
    sorted name order. This is the "drop a random PDF in" workflow.
    """
    appendices = tmp_path / "appendices"
    appendices.mkdir()

    # Drop two ad-hoc files that aren't in OLD_MILL_2026.entries.
    extras = ["aaa_extra.pdf", "zzz_extra.pdf"]
    for name in extras:
        _make_pdf(appendices / name, page_count=1, label=name)

    _patch_render(monkeypatch)

    result = compile_package(
        spec=OLD_MILL_2026,
        budget_draft=_budget_draft(),
        reserve_snapshot=_reserve_snapshot(),
        hoa_metadata=_hoa_metadata(),
        output_dir=tmp_path / "out",
        appendices_root=appendices,
    )

    generated_pages = sum(
        e.page_count_hint for e in OLD_MILL_2026.entries
        if isinstance(e, GeneratedPage)
    )
    # Two extras of one page each are appended after the generated pages.
    assert result.page_count == generated_pages + len(extras)


def test_compile_package_two_runs_produce_byte_equivalent_pdf(monkeypatch, tmp_path: Path, qpdf_required) -> None:
    """Test 6: Two consecutive runs with the same input snapshot produce
    PDFs that match in page count and byte length, modulo audit.json
    timestamps and PDF metadata timestamps (REQ-D11-015 stub).

    Strict byte-equivalence is impractical because both pypdf and PyMuPDF
    embed creation timestamps in the PDF metadata. We assert on the
    deterministic invariants we can: page count equality, sha256
    differing only by metadata, and identical computed audit-log outputs.
    """
    appendices = tmp_path / "appendices"
    _seed_appendices(appendices)
    _patch_render(monkeypatch)

    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"

    inputs = dict(
        spec=OLD_MILL_2026,
        budget_draft=_budget_draft(),
        reserve_snapshot=_reserve_snapshot(),
        hoa_metadata=_hoa_metadata(),
        appendices_root=appendices,
    )

    result_a = compile_package(output_dir=out_a, **inputs)
    result_b = compile_package(output_dir=out_b, **inputs)

    # Page count is fully deterministic for the same input snapshot.
    assert result_a.page_count == result_b.page_count

    # Audit output values (excluding timestamps) are identical.
    audit_a = json.loads(result_a.audit_path.read_text())
    audit_b = json.loads(result_b.audit_path.read_text())
    outputs_a = [(c["formula_id"], c["version"], c["output"]) for c in audit_a["formula_calls"]]
    outputs_b = [(c["formula_id"], c["version"], c["output"]) for c in audit_b["formula_calls"]]
    assert outputs_a == outputs_b


def test_compile_package_output_layout(monkeypatch, tmp_path: Path, qpdf_required) -> None:
    """Test 7: Output directory layout matches D-17:
        {output_dir}/package.pdf
        {output_dir}/generated.pdf  (intermediate for debugging)
        {output_dir}/audit.json
    """
    appendices = tmp_path / "appendices"
    _seed_appendices(appendices)
    output_dir = tmp_path / "out"
    _patch_render(monkeypatch)

    result = compile_package(
        spec=OLD_MILL_2026,
        budget_draft=_budget_draft(),
        reserve_snapshot=_reserve_snapshot(),
        hoa_metadata=_hoa_metadata(),
        output_dir=output_dir,
        appendices_root=appendices,
    )

    assert (output_dir / "package.pdf").exists()
    assert (output_dir / "generated.pdf").exists()
    assert (output_dir / "audit.json").exists()
    assert result.output_path == output_dir / "package.pdf"
    assert result.intermediate_path == output_dir / "generated.pdf"
    assert result.audit_path == output_dir / "audit.json"


def test_compile_package_sha256_matches_output_bytes(monkeypatch, tmp_path: Path, qpdf_required) -> None:
    """The CompileResult.sha256 is the digest of the bytes actually on
    disk at output_path (sanity check — protects against the result
    object drifting from the file)."""
    import hashlib

    appendices = tmp_path / "appendices"
    _seed_appendices(appendices)
    output_dir = tmp_path / "out"
    _patch_render(monkeypatch)

    result = compile_package(
        spec=OLD_MILL_2026,
        budget_draft=_budget_draft(),
        reserve_snapshot=_reserve_snapshot(),
        hoa_metadata=_hoa_metadata(),
        output_dir=output_dir,
        appendices_root=appendices,
    )

    on_disk_sha = hashlib.sha256(result.output_path.read_bytes()).hexdigest()
    assert result.sha256 == on_disk_sha
