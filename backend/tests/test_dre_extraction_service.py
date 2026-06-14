"""Tests for operator-triggered DRE extraction service behavior."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from app.dre_extraction.pipeline import DREExtractionRunRecord


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


def _seed_placeholder_run(db_path: Path) -> tuple[int, int, int]:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('A', 10)")
    property_id = conn.execute("SELECT id FROM properties").fetchone()[0]
    conn.execute(
        "INSERT INTO dre_documents (property_id, file_id, file_name, status, page_count) "
        "VALUES (?, 'dre/1/x.pdf', 'x.pdf', 'active', 18)",
        (property_id,),
    )
    document_id = conn.execute("SELECT id FROM dre_documents").fetchone()[0]
    conn.execute(
        """
        INSERT INTO dre_extraction_runs (
            dre_document_id, property_id, model_name, prompt_version, prompt_sha256,
            status, job_status
        ) VALUES (?, ?, 'g-flash', '1.0', 'sha', 'failed', 'queued')
        """,
        (document_id, property_id),
    )
    run_id = conn.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]
    conn.commit()
    conn.close()
    return property_id, document_id, run_id


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
        return DREExtractionRunRecord(
            model_name="gemini-test",
            prompt_version="1.0",
            prompt_sha256="sha",
            parsed_json={"ok": True},
            status="succeeded",
        )

    monkeypatch.setattr(svc, "run_dre_extraction", fake_run_dre_extraction)
    db_path = tmp_path / "test.db"
    property_id, document_id, run_id = _seed_placeholder_run(db_path)

    svc.run_extraction_job(
        run_id=run_id,
        property_id=property_id,
        dre_document_id=document_id,
        file_id="dre/9/large-dre.pdf",
        db_path=str(db_path),
    )

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT job_status, status, parsed_json, completed_at "
        "FROM dre_extraction_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    conn.close()

    assert captured["max_pages"] is None
    assert captured["page_count"] == 270
    assert row[0] == "completed"
    assert row[1] == "succeeded"
    assert row[2] == '{"ok": true}'
    assert row[3] is not None


def test_extraction_job_marks_placeholder_failed_on_crash(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.services import dre_extraction_service as svc

    pdf_path = tmp_path / "large-dre.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    db_path = tmp_path / "test.db"
    property_id, document_id, run_id = _seed_placeholder_run(db_path)

    monkeypatch.setattr(svc, "gemini_client_from_env", lambda: object())
    monkeypatch.setattr(svc, "default_model_name", lambda: "gemini-test")
    monkeypatch.setattr(svc, "dre_file_path", lambda file_id: pdf_path)
    monkeypatch.setattr(
        svc,
        "render_dre_pages",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    svc.run_extraction_job(
        run_id=run_id,
        property_id=property_id,
        dre_document_id=document_id,
        file_id="dre/9/large-dre.pdf",
        db_path=str(db_path),
    )

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT job_status, status, error_message, completed_at "
        "FROM dre_extraction_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    conn.close()

    assert row[0] == "failed"
    assert row[1] == "failed"
    assert "boom" in row[2]
    assert row[3] is not None


def test_gemini_client_from_env_uses_vertex_mode_when_enterprise_enabled(
    monkeypatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "budgeting-01")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")

    import importlib
    from app import config as config_module
    importlib.reload(config_module)
    from app.dre_extraction import gemini_callbacks as callbacks
    importlib.reload(callbacks)

    captured: dict[str, object] = {}

    class FakeClient:
        pass

    def fake_client(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr("google.genai.Client", fake_client)

    client = callbacks.gemini_client_from_env()

    assert isinstance(client, FakeClient)
    assert captured["vertexai"] is True
    assert captured["project"] == "budgeting-01"
    assert captured["location"] == "global"


def test_gemini_client_from_env_returns_none_without_api_key_in_local_mode(
    monkeypatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "false")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)

    import importlib
    from app import config as config_module
    importlib.reload(config_module)
    from app.dre_extraction import gemini_callbacks as callbacks
    importlib.reload(callbacks)

    assert callbacks.gemini_client_from_env() is None


def test_default_model_name_uses_settings_model(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")

    import importlib
    from app import config as config_module
    importlib.reload(config_module)
    from app.dre_extraction import gemini_callbacks as callbacks
    importlib.reload(callbacks)

    assert callbacks.default_model_name() == "gemini-3.5-flash"
