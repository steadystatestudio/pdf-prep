"""Fetch fixed, digest-verified AppImage build inputs. Never executes downloads.

Adapted from Steady State Studio's MIT-licensed File Renamer packaging helper:
https://github.com/steadystatestudio/file-renamer/tree/342f2cebf672578ff27f299eda466da7c2df4b96
Copyright (c) 2026 Steady State Studio. See LICENSE for the full MIT notice.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"
SHA256 = "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
SIZE = 15_092_216
# appimagetool would otherwise fetch a mutable latest runtime at packaging time.
RUNTIME_URL = "https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64"
RUNTIME_SHA256 = "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"
RUNTIME_SIZE = 944_632
# Both digests and sizes checked against the official release API on 2026-10-06:
# https://api.github.com/repos/AppImage/appimagetool/releases/tags/1.9.1
# https://api.github.com/repos/AppImage/type2-runtime/releases/tags/20251108
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024


def verify_file(path: Path, digest: str, size: int) -> bool:
    """Reject symlinks, non-files, truncated files and changed upstream bytes."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == digest


def download_verified(target: Path, url: str, digest: str, size: int, *, executable: bool = False) -> Path:
    """Atomically install a bounded HTTPS download only after its SHA-256 matches."""
    if not url.startswith("https://") or not 0 < size <= MAX_DOWNLOAD_BYTES:
        raise ValueError("A bounded HTTPS download is required")
    if target.is_symlink():
        raise ValueError("Refusing a symlink download target")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not verify_file(target, digest, size):
        temporary = None
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "PDF-Prep-build/1.0"})
            with urllib.request.urlopen(request, timeout=120) as response:
                if not response.geturl().startswith("https://"):
                    raise RuntimeError("Refusing an insecure download redirect")
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".appimage-", suffix=".download", delete=False) as stream:
                    temporary = Path(stream.name)
                    downloaded = 0
                    while chunk := response.read(1024 * 1024):
                        downloaded += len(chunk)
                        if downloaded > size:
                            raise RuntimeError("AppImage download exceeds its pinned size")
                        stream.write(chunk)
            if not verify_file(temporary, digest, size):
                raise RuntimeError("AppImage checksum mismatch; review upstream before changing the pin")
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    target.chmod(0o755 if executable else 0o644)
    return target


def main() -> int:
    directory = ROOT / "build/tools"
    if not directory.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError("Build tools directory escaped the project")
    download_verified(directory / "appimagetool", URL, SHA256, SIZE, executable=True)
    download_verified(directory / "runtime-x86_64", RUNTIME_URL, RUNTIME_SHA256, RUNTIME_SIZE)
    print(f"Verified AppImage tool and runtime: {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

