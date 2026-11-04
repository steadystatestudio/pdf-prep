"""Build the isolated Linux GTK probe without any GTK development dependency."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build() -> Path | None:
    if sys.platform != "linux":
        return None
    compiler = shutil.which(os.environ.get("CC", "cc"))
    if compiler is None:
        raise RuntimeError("A native C compiler is required to build the bundled appearance probe")
    directory = ROOT / "build/native"
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "appearance-probe"
    source = ROOT / "scripts/native_appearance_probe.c"
    subprocess.run([
        compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-fstack-protector-strong",
        "-D_FORTIFY_SOURCE=2", "-Wl,-z,relro,-z,now", str(source), "-ldl", "-o", str(output),
    ], check=True)
    output.chmod(0o755)
    (directory / "appearance-probe.json").write_text(json.dumps({
        "schema": 1,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "host_python_required": False,
        "host_pygobject_required": False,
        "optional_runtime_provider": "GTK 3 loaded dynamically in this isolated process",
    }, indent=2) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    result = build()
    print(result if result else "Native desktop probe is Linux-only")


def appearance_datas(root, package):
    """Ship executable data without recursively collecting host GTK libraries."""
    if sys.platform != "linux":
        return []
    probe = root / "build/native/appearance-probe"
    if not probe.is_file():
        raise RuntimeError("Build the native appearance probe before packaging")
    return [(str(probe), package + "/assets"),
            (str(root / "build/native/appearance-probe.json"), package + "/assets"),
            (str(root / "scripts/native_appearance_probe.c"), package + "/assets")]


def stage_probe(bundle, package):
    if sys.platform != "linux":
        return
    directory = bundle / "_internal" / package / "assets"
    probe = directory / "appearance-probe"
    probe.chmod(0o755)
    record = json.loads((directory / "appearance-probe.json").read_text(encoding="utf-8"))
    if hashlib.sha256(probe.read_bytes()).hexdigest() != record["binary_sha256"]:
        raise RuntimeError("Bundled appearance probe does not match build metadata")
    if hashlib.sha256((directory / "native_appearance_probe.c").read_bytes()).hexdigest() != record["source_sha256"]:
        raise RuntimeError("Bundled appearance probe source does not match build metadata")
