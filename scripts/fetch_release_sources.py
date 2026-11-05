"""Fetch checksum-pinned matching sources without unpacking or executing them."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as content:
        return hashlib.file_digest(content, "sha256").hexdigest()


def source_index(root=ROOT):
    index = json.loads((root / "SOURCE-INDEX.json").read_text(encoding="utf-8"))
    lock = (root / "requirements-build.txt").read_text(encoding="utf-8")
    versions = index["binding_versions"]
    if (set(versions) != {"PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6",
                         "PySide6_WebEngine", "PySide6_Pdf"}
            or any(versions[name] != index["qt_version"] for name in
                   ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6"))):
        raise ValueError("Matching-source binding version inventory is incomplete")
    for name, version in versions.items():
        if not re.search(rf"(?mi)^{name}=={re.escape(version)}$", lock):
            raise ValueError("Matching sources differ from the dependency pins")
    prefixes = ("https://download.qt.io/official_releases/", "https://codeload.github.com/AppImage/type2-runtime/", "https://codeload.github.com/vasi/squashfuse/", "https://github.com/libfuse/libfuse/releases/download/")
    for item in index["archives"]:
        if (Path(item["name"]).name != item["name"] or "/" in item["name"] or "\\" in item["name"]
                or not item["url"].startswith(prefixes) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or not 0 < item["size"] <= 1_500_000_000):
            raise ValueError("Invalid source archive pin")
    return index


def download(item, destination):
    target = destination / item["name"]
    if target.is_symlink():
        raise ValueError("Refusing linked source destination")
    if target.is_file() and target.stat().st_size == item["size"] and digest(target) == item["sha256"]:
        return target
    destination.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        request = urllib.request.Request(item["url"], headers={"User-Agent": "Steady-State-Studio-source-mirror"})
        with urllib.request.urlopen(request, timeout=180) as response:
            if not response.geturl().startswith("https://"):
                raise RuntimeError("Insecure source redirect")
            with tempfile.NamedTemporaryFile(dir=destination, suffix=".download", delete=False) as output:
                temporary = Path(output.name)
                count = 0
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if count > item["size"]:
                        raise RuntimeError("Source exceeds pinned size")
                    output.write(chunk)
        if temporary.stat().st_size != item["size"] or digest(temporary) != item["sha256"]:
            raise RuntimeError("Source checksum mismatch")
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist/release-stage")
    args = parser.parse_args(argv)
    index = source_index()
    for item in index["archives"]:
        download(item, args.output_dir)
        print("Verified " + item["name"])
    (args.output_dir / "SOURCE-INDEX.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
