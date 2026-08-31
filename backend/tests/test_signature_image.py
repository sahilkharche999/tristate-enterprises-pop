"""Firm signature image with optional per-HOA override."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import signature_storage


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    monkeypatch.setattr(signature_storage, "_storage_root", lambda: tmp_path)
    return tmp_path


def test_rejects_svg():
    with pytest.raises(signature_storage.UnsupportedSignatureFileType):
        signature_storage.save_firm_signature(
            file_bytes=b"<svg></svg>",
            original_filename="sign.svg",
        )


def test_hoa_override_beats_firm(storage_root):
    firm = signature_storage.save_firm_signature(
        file_bytes=PNG_BYTES, original_filename="firm.png"
    )
    hoa = signature_storage.save_hoa_signature(
        property_id=11, file_bytes=PNG_BYTES, original_filename="hoa.png"
    )
    chosen = signature_storage.resolve_signature_filename(
        hoa_filename=hoa, firm_filename=firm
    )
    assert chosen == hoa
    uri = signature_storage.signature_data_uri(chosen)
    assert uri is not None
    assert uri.startswith("data:image/png;base64,")


def test_falls_back_to_firm_when_hoa_missing(storage_root):
    firm = signature_storage.save_firm_signature(
        file_bytes=PNG_BYTES, original_filename="firm.png"
    )
    chosen = signature_storage.resolve_signature_filename(
        hoa_filename="signatures/hoa/99/signature.png",
        firm_filename=firm,
    )
    assert chosen == firm


def test_missing_image_resolves_to_none(storage_root):
    assert (
        signature_storage.resolve_signature_filename(
            hoa_filename=None, firm_filename=None
        )
        is None
    )
    assert signature_storage.signature_data_uri(None) is None


def test_inject_omits_img_when_no_uri():
    html = '<div class="letter-signature"><p>Board</p></div>'
    assert signature_storage.inject_signature_image(html, None) == html


def test_inject_places_img_in_closer():
    html = '<div class="letter-signature"><p>Board</p></div>'
    out = signature_storage.inject_signature_image(
        html, "data:image/png;base64,xx"
    )
    assert 'class="letter-signature-image"' in out
    assert out.index("<img") < out.index("Board")
