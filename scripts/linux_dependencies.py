"""Use PyInstaller's supported system-library exclusion and record prerequisites.

The manifest names OS packages/libraries, never private absolute build paths.
Ubuntu supplies these libraries; the application does not redistribute them.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def package_owner(source: str) -> str:
    # dpkg may record /lib while a merged-/usr host resolves it to /usr/lib.
    candidates = [source, str(Path(source).resolve())]
    if source.startswith("/usr/lib/"):
        candidates.append(source.removeprefix("/usr"))
    for candidate in dict.fromkeys(candidates):
        result = subprocess.run(["dpkg-query", "--search", candidate], capture_output=True, text=True)
        if result.returncode == 0:
            owner = result.stdout.splitlines()[0].split(": ", 1)[0].split(":", 1)[0]
            if re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", owner):
                return owner
    raise RuntimeError(f"Cannot identify Ubuntu package for system library {Path(source).name}")


def omit_system_libraries(analysis, manifest: Path) -> None:
    before = list(analysis.binaries)
    # Official API preserves Python/lib-dynload and non-system wheel binaries.
    # No filename-based deletion is performed on the completed package.
    analysis.exclude_system_libraries()
    retained = set(analysis.binaries)
    omitted = [entry for entry in before if entry not in retained]
    libraries = []
    for destination, source, kind in omitted:
        libraries.append({"library": Path(destination).name, "package": package_owner(source)})
    packages = sorted({entry["package"] for entry in libraries})
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "policy": "OS libraries excluded using PyInstaller Analysis.exclude_system_libraries",
        "build_distribution": "Ubuntu 22.04; compatible Ubuntu 24.04 package equivalents are accepted",
        "packages": packages,
        "libraries": sorted(libraries, key=lambda entry: entry["library"]),
        "install": "sudo apt-get install --no-install-recommends " + " ".join(packages),
    }, indent=2) + "\n", encoding="utf-8")


def install_prerequisites(manifest: Path) -> None:
    packages = json.loads(manifest.read_text(encoding="utf-8"))["packages"]
    if (not packages or not isinstance(packages, list)
            or any(not isinstance(item, str) or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", item) for item in packages)):
        raise ValueError("Invalid system package manifest")
    subprocess.run(["sudo", "apt-get", "install", "--no-install-recommends", "-y", *packages], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    install_prerequisites(parser.parse_args().manifest)
