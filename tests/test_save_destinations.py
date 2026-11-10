"""Saving a new PDF on filesystems without hard links, with ordinary permissions."""
import errno
import os
import stat

import pytest

from pdf_prep import engine
from pdf_prep.engine import save_new_pdf

PDF = b"%PDF-1.4\n%synthetic\n"


def keeps_permissions(folder):
    """FAT and exFAT mounts have no permission bits to check."""
    sample = folder / "permission-probe"
    sample.write_bytes(b"")
    sample.chmod(0o600)
    kept = stat.S_IMODE(sample.stat().st_mode) == 0o600
    sample.unlink()
    return kept


def leftovers(folder):
    return [path.name for path in folder.iterdir() if path.name.startswith(".pdf-prep-")]


def test_save_creates_the_file_once_and_refuses_to_replace_it(tmp_path):
    saved = save_new_pdf(PDF, tmp_path / "result.pdf")
    assert saved.read_bytes() == PDF and not leftovers(tmp_path)
    with pytest.raises(FileExistsError):
        save_new_pdf(b"%PDF-1.4\nother\n", tmp_path / "result.pdf")
    assert saved.read_bytes() == PDF and not leftovers(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_saved_pdf_has_ordinary_permissions_not_owner_only(tmp_path):
    if not keeps_permissions(tmp_path):
        pytest.skip("filesystem has no permission bits")
    previous = os.umask(0o022)
    try:
        saved = save_new_pdf(PDF, tmp_path / "result.pdf")
    finally:
        os.umask(previous)
    assert stat.S_IMODE(saved.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name == "nt", reason="POSIX commit fallbacks")
@pytest.mark.parametrize("link_error", [errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS])
def test_save_works_where_no_replace_rename_and_hard_links_are_unsupported(tmp_path, monkeypatch, link_error):
    def no_links(source, target):
        raise OSError(link_error, os.strerror(link_error))

    monkeypatch.setattr(engine, "_rename_no_replace", lambda source, target: False)
    monkeypatch.setattr(engine.os, "link", no_links)
    saved = save_new_pdf(PDF, tmp_path / "result.pdf")
    assert saved.read_bytes() == PDF and not leftovers(tmp_path)
    with pytest.raises(FileExistsError):
        save_new_pdf(b"%PDF-1.4\nother\n", tmp_path / "result.pdf")
    assert saved.read_bytes() == PDF


@pytest.mark.skipif(os.name == "nt", reason="POSIX commit fallbacks")
def test_hard_link_path_still_refuses_an_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_rename_no_replace", lambda source, target: False)
    saved = save_new_pdf(PDF, tmp_path / "result.pdf")
    with pytest.raises(FileExistsError):
        save_new_pdf(b"%PDF-1.4\nother\n", tmp_path / "result.pdf")
    assert saved.read_bytes() == PDF and not leftovers(tmp_path)
