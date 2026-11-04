"""Build native portable Windows or Linux x86-64 packages."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path

import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_appearance_probe import build as build_appearance_probe, stage_probe
import fetch_appimagetool as appimage_inputs

ROOT = Path(__file__).resolve().parents[1]
NAME = "PDF-Prep"
LICENSE_PACKAGES = ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6",
                    "Pillow", "pypdf", "pypdfium2", "PyInstaller")


def confined_output(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if resolved == root.resolve() or not resolved.is_relative_to(root.resolve()):
        raise RuntimeError(f"Build output escaped the project: {path.name}")
    return path


def pyinstaller_environment(target_platform: str) -> dict[str, str]:
    # Only the build child receives this PATH. Inherited tools can otherwise
    # inject incompatible same-named ICU/UCRT DLLs into a Windows package.
    environment = dict(os.environ)
    if target_platform == "win32":
        windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        paths = [Path(sys.executable).resolve().parent, Path(sys.base_prefix),
                 Path(sys.base_prefix) / "DLLs", windows / "System32", windows]
        environment["PATH"] = os.pathsep.join(dict.fromkeys(str(path) for path in paths))
    return environment


def verify_qt_subset(bundle: Path) -> None:
    excluded = ("qt6pdf", "qt6virtualkeyboard", "qt6qml", "qt6quick", "qt6webengine", "qt6network")
    for path in (bundle / "_internal").rglob("*"):
        name = path.name.lower().removeprefix("lib")
        if path.is_file() and (name.startswith(excluded) or name == "opengl32sw.dll"
                or name.startswith(("qpdf.", "qtvirtualkeyboardplugin.", "qtuiotouchplugin.", "qvnc."))):
            raise RuntimeError(f"Unreviewed optional Qt component in package: {path.name}")


def python_license() -> Path:
    for source in (Path(sys.base_prefix) / "LICENSE.txt", Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"):
        if source.is_file():
            return source
    raise RuntimeError("Python license text is missing from the build interpreter")


def source_revision(root: Path) -> str:
    revision = os.environ.get("BUILD_SOURCE_COMMIT", "")
    if not revision:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise RuntimeError("BUILD_SOURCE_COMMIT must identify a full Git commit")
    return revision.lower()


def source_hashes(root: Path) -> dict[str, str]:
    hashes = {}
    paths = [root / name for name in ("pyproject.toml", "requirements-build.txt", "SOURCE-INDEX.json")]
    for directory in ("src", "scripts", "packaging", ".github/workflows"):
        paths.extend((root / directory).rglob("*"))
    for item in sorted(set(paths)):
        if (item.is_file() and not item.is_symlink() and "__pycache__" not in item.parts
                and item.suffix not in (".pyc", ".pyo")):
            hashes[item.relative_to(root).as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
    return hashes


def copy_notices(root: Path, package: Path, target_platform: str) -> None:
    from PySide6.QtCore import qVersion
    source_index = json.loads((root / "SOURCE-INDEX.json").read_text(encoding="utf-8"))
    if qVersion() != source_index["qt_version"]:
        raise RuntimeError("Qt runtime differs from the matching-source pins")
    licenses = package / "licenses"
    licenses.mkdir(exist_ok=True)
    versions = {}
    for name in LICENSE_PACKAGES:
        distribution = metadata.distribution(name)
        versions[name] = distribution.version
        for item in distribution.files or []:
            rel = str(item).replace("\\", "/")
            if ".dist-info/licenses/" in rel:
                destination = licenses / name / rel.split(".dist-info/licenses/", 1)[1]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(distribution.locate_file(item), destination)
    shutil.copytree(root / "packaging/licenses", licenses, dirs_exist_ok=True)
    (licenses / "Python").mkdir(exist_ok=True)
    shutil.copyfile(python_license(), licenses / "Python/LICENSE.txt")
    for name in ("LICENSE", "README.md", "THIRD-PARTY.md", "SOURCE-INDEX.json"):
        shutil.copyfile(root / name, package / name)
    if (root / "docs/APPEARANCE.md").is_file():
        (package / "docs").mkdir(exist_ok=True)
        shutil.copy2(root / "docs/APPEARANCE.md", package / "docs/APPEARANCE.md")
    if target_platform == "linux":
        shutil.copyfile(root / "build/LINUX-SYSTEM-DEPENDENCIES.json", package / "LINUX-SYSTEM-DEPENDENCIES.json")
        shutil.copyfile(root / "packaging/linux/README-LINUX.txt", package / "README-LINUX.txt")
        shutil.copyfile(root / "packaging/linux/APPIMAGE_RUNTIME_LICENSE.txt", licenses / "APPIMAGE_RUNTIME_LICENSE.txt")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    info = {"product": "PDF Prep", "version": project["project"]["version"],
            "python": platform.python_version(), "dependencies": versions,
            "platform": target_platform, "os": platform.platform(), "machine": platform.machine(),
            "unsigned": True, "source_commit": source_revision(root),
            "source_tree": subprocess.check_output(["git", "rev-parse", source_revision(root) + "^{tree}"], cwd=root, text=True).strip(),
            "source_hashes": source_hashes(root),
            "appimage_inputs": {"appimagetool": {"url": appimage_inputs.URL, "sha256": appimage_inputs.SHA256},
                                "runtime": {"url": appimage_inputs.RUNTIME_URL, "sha256": appimage_inputs.RUNTIME_SHA256}}
            if target_platform == "linux" else None}
    (package / "BUILD-INFO.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    license_index = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(licenses.rglob("*")) if p.is_file()}
    (package / "LICENSE-INDEX.json").write_text(json.dumps(license_index, indent=2), encoding="utf-8")


def make_appdir(root: Path, bundle: Path) -> Path:
    appdir = confined_output(root / "build", root / "build" / f"{NAME}.AppDir")
    if appdir.exists():
        shutil.rmtree(appdir)
    shutil.copytree(bundle, appdir / "usr/lib/pdf-prep", symlinks=True)
    for name in ("AppRun", "pdf-prep.desktop", "pdf-prep.svg"):
        shutil.copyfile(root / "packaging/linux" / name, appdir / name)
    (appdir / "AppRun").chmod(0o755)
    return appdir


def package_appimage(root: Path, bundle: Path) -> Path:
    tool = Path(os.environ.get("APPIMAGETOOL", str(root / "build/tools/appimagetool")))
    runtime = Path(os.environ.get("APPIMAGE_RUNTIME", str(root / "build/tools/runtime-x86_64")))
    for path, digest, size in ((tool, appimage_inputs.SHA256, appimage_inputs.SIZE),
                               (runtime, appimage_inputs.RUNTIME_SHA256, appimage_inputs.RUNTIME_SIZE)):
        if not appimage_inputs.verify_file(path, digest, size):
            raise RuntimeError("Missing or unverified AppImage inputs; run python scripts/fetch_appimagetool.py")
    appdir = make_appdir(root, bundle)
    output = confined_output(root, root / "dist" / f"{NAME}-x86_64.AppImage")
    environment = dict(os.environ, ARCH="x86_64", APPIMAGE_EXTRACT_AND_RUN="1")
    subprocess.run([str(tool.resolve()), "--runtime-file", str(runtime.resolve()), str(appdir), str(output)],
                   cwd=root, env=environment, check=True, timeout=1200)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("AppImage packaging did not create an output")
    return output


def make_archive(root: Path, target_platform: str) -> Path:
    suffix, format_name = ("windows-x86_64", "zip") if target_platform == "win32" else ("linux-x86_64", "gztar")
    base = confined_output(root, root / "dist" / f"{NAME}-{suffix}")
    return Path(shutil.make_archive(str(base), format_name, root_dir=root / "dist", base_dir=NAME))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-only", action="store_true", help="Create the portable archive without an AppImage")
    arguments = parser.parse_args(argv)
    if sys.platform not in {"win32", "linux"} or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise SystemExit("Run this build on native Windows or Linux x86-64.")
    root = ROOT.resolve()
    build = confined_output(root, root / "build")
    dist = confined_output(root, root / "dist")
    for path in (build / "pyinstaller", build / "pyinstaller-cache", dist / NAME,
                 dist / f"{NAME}-windows-x86_64.zip", dist / f"{NAME}-linux-x86_64.tar.gz",
                 dist / f"{NAME}-x86_64.AppImage", dist / "SHA256SUMS.txt"):
        confined_output(root, path)
    build.mkdir(exist_ok=True)
    build_appearance_probe()
    dist.mkdir(exist_ok=True)
    os.environ["PYINSTALLER_CONFIG_DIR"] = str(build / "pyinstaller-cache")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(dist), "--workpath", str(build / "pyinstaller"),
                    str(root / "packaging/PDF-Prep.spec")], cwd=root,
                   env=pyinstaller_environment(sys.platform), check=True, timeout=1200)
    bundle = dist / NAME
    stage_probe(bundle, "pdf_prep")
    verify_qt_subset(bundle)
    copy_notices(root, bundle, sys.platform)
    artifacts = [make_archive(root, sys.platform)]
    if sys.platform == "linux" and not arguments.bundle_only:
        artifacts.append(package_appimage(root, bundle))
    lines = []
    for path in artifacts:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        lines.append(f"{digest}  {path.name}\n")
        print(f"Built {path}")
    (dist / "SHA256SUMS.txt").write_text("".join(lines), encoding="ascii")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
