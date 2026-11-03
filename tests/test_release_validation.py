"""Release gates use synthetic files only and never contact GitHub."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_validation as release
import fetch_release_sources as sources


def test_reviewed_source_requires_exact_commit_and_both_studio_identities(monkeypatch):
    sha = "a" * 40
    identity = "steadystatestudio <300259075+steadystatestudio@users.noreply.github.com>"
    def git(*args):
        if args[:2] == ("rev-parse", "HEAD"):
            return sha
        if args[0] == "show":
            return identity + "\n" + identity
        return "b" * 40
    monkeypatch.setattr(release, "git", git)
    assert release.validate_source("v1.0.4", sha) == "b" * 40
    for tag, commit in (("v1.0.0", sha), ("v1.0.4", "main"), ("v1.0.4", "c" * 40)):
        with pytest.raises(ValueError):
            release.validate_source(tag, commit)
    monkeypatch.setattr(release, "git", lambda *args: "Other <user@example.invalid>\n" + identity if args[0] == "show" else git(*args))
    with pytest.raises(ValueError, match="author or committer"):
        release.validate_source("v1.0.4", sha)


def test_package_provenance_checks_both_source_ids_and_all_bindings():
    info = {"product": "PDF Prep", "version": "1.0.3", "source_commit": "a" * 40,
            "source_tree": "b" * 40, "platform": "linux",
            "dependencies": {name: "6.11.2" for name in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6")}}
    release.check_info(info, "a" * 40, "b" * 40, "linux")
    for field, value in (("source_commit", "c" * 40), ("source_tree", None), ("platform", "win32"), ("version", "1.0.0")):
        with pytest.raises(ValueError, match="provenance"):
            release.check_info({**info, field: value}, "a" * 40, "b" * 40, "linux")
    info["dependencies"]["PySide6_Addons"] = "6.11.0"
    with pytest.raises(ValueError, match="provenance"):
        release.check_info(info, "a" * 40, "b" * 40, "linux")


def test_wrong_run_missing_sources_or_extra_assets_cannot_pass_receipt(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    expected = release.asset_names("Sources") | {"SHA256SUMS-Sources.txt"}
    receipt = {"source_commit": "a" * 40, "source_tree": "b" * 40, "platform": "Sources",
               "run_id": "42", "files": [{"name": name} for name in expected]}
    release.check_receipt(receipt, "a" * 40, "b" * 40, "Sources")
    for changed in ({"run_id": "41"}, {"files": receipt["files"][:-1]}, {"files": [*receipt["files"], {"name": "unreviewed.txt"}]}):
        with pytest.raises(ValueError, match="Receipt"):
            release.check_receipt({**receipt, **changed}, "a" * 40, "b" * 40, "Sources")


class Response(io.BytesIO):
    def geturl(self):
        return "https://download.qt.io/fixture"


def test_bad_source_download_cannot_replace_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "source.tar.xz"
    target.write_bytes(b"original")
    item = {"name": target.name, "url": "https://download.qt.io/fixture", "size": 4,
            "sha256": hashlib.sha256(b"good").hexdigest()}
    monkeypatch.setattr(sources.urllib.request, "urlopen", lambda *a, **k: Response(b"bad!"))
    with pytest.raises(RuntimeError, match="checksum"):
        sources.download(item, tmp_path)
    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.download"))


def test_matching_sources_and_manual_workflow_keep_publication_separate():
    index = sources.source_index()
    assert index["qt_version"] == "6.11.2" and len(index["archives"]) == 5
    workflow = (ROOT / ".github/workflows/build-release.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow and "expected_commit:" in workflow
    assert "pdf-prep-builds" in workflow and "refs/heads/main" in workflow
    assert "needs: [prepare, build, sources, compatibility]" in workflow
    assert "release_validation.py finalize" in workflow
    assert "gh release" not in workflow and "contents: write" not in workflow
