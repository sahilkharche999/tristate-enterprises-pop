"""Tests for operator-triggered DRE extraction service behavior."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_extraction_job_renders_all_pages_by_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.services import dre_extraction_service as svc

    pdf_path = tmp_path / "large-dre.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    captured: dict[str, object] = {}

    monkeypatch.setattr(svc, "gemini_client_from_env", lambda: object())
    monkeypatch.setattr(svc, "default_model_name", lambda: "gemini-test")
    monkeypatch.setattr(svc, "dre_file_path", lambda file_id: pdf_path)

    def fake_render(path: str, max_pages=None):
        captured["render_path"] = path
        captured["max_pages"] = max_pages
        return [
            SimpleNamespace(page_number=i, content=b"png", mime_type="image/png")
            for i in range(1, 271)
        ]

    monkeypatch.setattr(svc, "render_dre_pages", fake_render)
    monkeypatch.setattr(
        svc,
        "build_classify_callback",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        svc,
        "build_extract_callback",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(svc, "build_repair_callback", lambda *args, **kwargs: object())

    def fake_run_dre_extraction(*, page_count, **kwargs):
        captured["page_count"] = page_count
        return SimpleNamespace(status="succeeded")

    monkeypatch.setattr(svc, "run_dre_extraction", fake_run_dre_extraction)
    monkeypatch.setattr(svc, "save_extraction_run", lambda *args, **kwargs: 123)

    class _Connection:
        def commit(self) -> None:
            captured["committed"] = True

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(svc, "_resolve_connection", lambda db_path: _Connection())

    svc.run_extraction_job(
        property_id=18,
        dre_document_id=9,
        file_id="dre/9/large-dre.pdf",
    )

    assert captured["max_pages"] is None
    assert captured["page_count"] == 270
    assert captured["committed"] is True
    assert captured["closed"] is True
