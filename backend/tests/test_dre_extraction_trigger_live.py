"""End-to-end live test for ``POST /hoa/{id}/dre/documents/{doc_id}/extract``.

Uploads a real DRE PDF, hits the trigger endpoint, then polls the
extraction-runs list until the BackgroundTask lands a row. Asserts the
new run carries the structured AssessmentSetup data the operator review
workbench expects.

Marked ``live`` so it stays out of the default CI sweep — runs only with
``pytest -m live`` and ``GEMINI_API_KEY`` set in the environment.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.live


_CORPUS_ROOT = Path(__file__).resolve().parents[2] / "DRE"
_TEST_PDF = _CORPUS_ROOT / "residential dre budget Esprit Park 10-16-13.pdf"


@pytest.mark.skipif(not _TEST_PDF.exists(), reason="Esprit Park DRE not in corpus")
@pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)
def test_trigger_endpoint_drives_full_extraction_against_real_gemini(
    client, db_session,
):
    """End-to-end: upload + trigger + poll until extraction row appears."""
    from app.ai_implementation.db.models import Property

    prop = db_session.query(Property).first()
    assert prop is not None
    hoa_id = prop.id

    # 1. Upload a real DRE PDF.
    with _TEST_PDF.open("rb") as fh:
        upload_response = client.post(
            f"/hoa/{hoa_id}/dre/upload",
            files={"file": (_TEST_PDF.name, fh, "application/pdf")},
        )
    assert upload_response.status_code == 200, upload_response.text
    document_id = upload_response.json()["dre_document_id"]

    # 2. Trigger extraction. TestClient runs the BackgroundTask
    #    synchronously after the response, so by the time .post() returns
    #    the extraction has run end-to-end through Gemini.
    trigger_response = client.post(
        f"/hoa/{hoa_id}/dre/documents/{document_id}/extract"
    )
    assert trigger_response.status_code == 202, trigger_response.text

    # 3. Poll the runs list. With TestClient + sync BackgroundTasks the
    #    run is already present on the first poll, but keep a short
    #    retry loop in case the engine batches commits.
    deadline = time.monotonic() + 180  # 3-minute ceiling for Gemini latency
    discovered_run = None
    while time.monotonic() < deadline:
        runs_response = client.get(f"/hoa/{hoa_id}/dre/extraction-runs")
        assert runs_response.status_code == 200, runs_response.text
        for run in runs_response.json():
            if run["dre_document_id"] == document_id:
                discovered_run = run
                break
        if discovered_run is not None:
            break
        time.sleep(2)

    assert discovered_run is not None, (
        f"No extraction run materialised for document {document_id}"
    )
    assert discovered_run["status"] in (
        "succeeded", "extraction_partial", "failed",
    ), f"Unexpected status: {discovered_run['status']}"
    # 4. Drill into the run detail — the operator review workbench reads
    #    parsed_json, so we assert it carries the structured shape.
    detail = client.get(
        f"/hoa/{hoa_id}/dre/extraction-runs/{discovered_run['extraction_run_id']}"
    ).json()
    assert detail["parsed_json"] is not None
    setup_type = (
        detail["parsed_json"].get("assessment_setup", {}).get("setup_type")
    )
    assert setup_type, "parsed_json missing assessment_setup.setup_type"
    assert isinstance(detail["parsed_json"].get("allocation_pools"), list)
