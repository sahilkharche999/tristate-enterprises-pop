"""DRE filesystem storage tests (Phase 3.1).

The storage layer keeps DRE PDFs on disk under
``BUDGET_STORAGE_ROOT/dre/<property_id>/`` with the row id prefixed so
multiple uploads for one HOA coexist. The DB row's ``file_id`` is the
relative path; this module also resolves and removes files.

Each test uses ``monkeypatch`` to point ``settings.BUDGET_STORAGE_ROOT``
at a temp directory, so no production state is touched.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.dre_extraction import storage as storage_module


@pytest.fixture
def tmp_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "app.config.settings.BUDGET_STORAGE_ROOT", str(tmp_path)
    )
    return tmp_path


class TestSaveDREFile:
    def test_writes_bytes_under_property_subdir(self, tmp_storage: Path) -> None:
        file_id = storage_module.save_dre_file(
            property_id=1,
            file_bytes=b"%PDF-1.4 fake DRE",
            original_filename="DRE Master Schedule.pdf",
            dre_document_id=42,
        )
        # file_id is a relative path
        assert file_id.startswith("dre/1/")
        # The actual file exists on disk
        absolute = tmp_storage / file_id
        assert absolute.exists()
        assert absolute.read_bytes() == b"%PDF-1.4 fake DRE"

    def test_filename_is_sanitized(self, tmp_storage: Path) -> None:
        file_id = storage_module.save_dre_file(
            property_id=1,
            file_bytes=b"x",
            original_filename="../../etc/passwd",
            dre_document_id=7,
        )
        # No upward traversal in the saved path
        assert ".." not in file_id
        assert file_id.startswith("dre/1/7_")

    def test_filename_with_spaces_and_special_chars(self, tmp_storage: Path) -> None:
        file_id = storage_module.save_dre_file(
            property_id=2,
            file_bytes=b"x",
            original_filename="A Year's   Mix [v2].pdf",
            dre_document_id=1,
        )
        assert "/" not in file_id.split("/")[-1]  # no slashes in the basename
        assert (tmp_storage / file_id).exists()

    def test_multiple_uploads_for_same_property_dont_collide(
        self, tmp_storage: Path
    ) -> None:
        f1 = storage_module.save_dre_file(
            property_id=1, file_bytes=b"first",
            original_filename="dre.pdf", dre_document_id=1,
        )
        f2 = storage_module.save_dre_file(
            property_id=1, file_bytes=b"second",
            original_filename="dre.pdf", dre_document_id=2,
        )
        assert f1 != f2
        assert (tmp_storage / f1).read_bytes() == b"first"
        assert (tmp_storage / f2).read_bytes() == b"second"


class TestResolveAndDelete:
    def test_dre_file_path_returns_absolute(self, tmp_storage: Path) -> None:
        file_id = storage_module.save_dre_file(
            property_id=1, file_bytes=b"x",
            original_filename="d.pdf", dre_document_id=1,
        )
        resolved = storage_module.dre_file_path(file_id)
        assert resolved.is_absolute()
        assert resolved.exists()

    def test_dre_file_exists_true_after_save(self, tmp_storage: Path) -> None:
        file_id = storage_module.save_dre_file(
            property_id=1, file_bytes=b"x",
            original_filename="d.pdf", dre_document_id=1,
        )
        assert storage_module.dre_file_exists(file_id)

    def test_dre_file_exists_false_for_missing(self, tmp_storage: Path) -> None:
        assert not storage_module.dre_file_exists("dre/1/999_nope.pdf")
        assert not storage_module.dre_file_exists(None)
        assert not storage_module.dre_file_exists("")

    def test_delete_removes_existing_file(self, tmp_storage: Path) -> None:
        file_id = storage_module.save_dre_file(
            property_id=1, file_bytes=b"x",
            original_filename="d.pdf", dre_document_id=1,
        )
        assert storage_module.delete_dre_file(file_id) is True
        assert not storage_module.dre_file_exists(file_id)

    def test_delete_missing_returns_false(self, tmp_storage: Path) -> None:
        assert storage_module.delete_dre_file("dre/1/000_gone.pdf") is False
