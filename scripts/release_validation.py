"""Verify local release artifacts. This helper never writes to GitHub."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tomllib
import zipfile

from fetch_release_sources import source_index

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ("Windows", "Linux", "Sources", "Ubuntu24")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def digest(path):
    with path.open("rb") as content:
        return hashlib.file_digest(content, "sha256").hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return path


def record(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError("Missing or linked artifact")
    return {"name": path.name, "size": path.stat().st_size, "sha256": digest(path)}


def validate_source(tag, commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or git("rev-parse", "HEAD") != commit:
        raise ValueError("The checkout must equal the full reviewed commit")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    module = ast.parse((ROOT / "src/pdf_prep/__init__.py").read_text(encoding="utf-8"))
    versions = [ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)]
    if tag != "v" + project["project"]["version"] or versions != [project["project"]["version"]]:
        raise ValueError("Tag and application versions differ")
    expected_identity = "steadystatestudio <300259075+steadystatestudio@users.noreply.github.com>"
    if git("show", "-s", "--format=%an <%ae>%n%cn <%ce>", commit).splitlines() != [expected_identity, expected_identity]:
        raise ValueError("Unexpected source author or committer")
    return git("rev-parse", commit + "^{tree}")


def check_info(info, commit, tree, platform):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if (info.get("product") != "PDF Prep" or info.get("version") != version
            or info.get("source_commit") != commit or info.get("source_tree") != tree
            or info.get("platform") != platform
            or any(info.get("dependencies", {}).get(name) != source_index()["qt_version"]
                   for name in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6"))):
        raise ValueError("Package provenance differs from the reviewed source")


def asset_names(platform):
    if platform == "Windows":
        return {"PDF-Prep-windows-x86_64.zip", "BUILD-INFO-Windows.json", "QA-Windows.zip"}
    if platform == "Linux":
        return {"PDF-Prep-linux-x86_64.tar.gz", "PDF-Prep-x86_64.AppImage", "BUILD-INFO-Linux.json", "QA-Linux.zip"}
    if platform == "Sources":
        return {"SOURCE-INDEX.json", *(item["name"] for item in source_index()["archives"])}
    return {"QA-Ubuntu24.zip"}


def check_receipt(receipt, commit, tree, platform):
    names = [item["name"] for item in receipt.get("files", [])]
    if (receipt.get("source_commit") != commit or receipt.get("source_tree") != tree
            or receipt.get("platform") != platform or receipt.get("run_id") != os.environ.get("GITHUB_RUN_ID")
            or len(names) != len(set(names)) or set(names) != asset_names(platform) | {f"SHA256SUMS-{platform}.txt"}):
        raise ValueError("Receipt has wrong source, run, platform or asset set")


def qa_archive(platform, destination):
    files = [ROOT / "artifacts/source-tests.xml"]
    prefixes = ("windows",) if platform == "Windows" else (("ubuntu22-portable", "ubuntu22-appimage") if platform == "Linux" else ("ubuntu24-portable", "ubuntu24-appimage"))
    for prefix in prefixes:
        files.append(ROOT / "artifacts" / (prefix + ".json"))
        files.extend((ROOT / "artifacts" / (prefix + "-artifacts")).rglob("*"))
    files = [path for path in files if path.is_file() and not path.is_symlink()]
    if not files:
        raise ValueError("No synthetic QA evidence")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT / "artifacts").as_posix())


def stage(args):
    directory = ROOT / "dist/release-stage"
    directory.mkdir(parents=True, exist_ok=True)
    if args.platform in ("Windows", "Linux"):
        info = json.loads((ROOT / "dist/PDF-Prep/BUILD-INFO.json").read_text(encoding="utf-8"))
        check_info(info, args.commit, args.tree, "win32" if args.platform == "Windows" else "linux")
        write(directory / f"BUILD-INFO-{args.platform}.json", info)
        for name in asset_names(args.platform):
            if name.startswith("PDF-Prep-"):
                shutil.copyfile(ROOT / "dist" / name, directory / name)
        qa_archive(args.platform, directory / f"QA-{args.platform}.zip")
    elif args.platform == "Sources":
        for item in source_index()["archives"]:
            if record(directory / item["name"]) != {key: item[key] for key in ("name", "size", "sha256")}:
                raise ValueError("Source archive differs from the official pin")
    else:
        qa_archive(args.platform, directory / "QA-Ubuntu24.zip")
    records = [record(directory / name) for name in sorted(asset_names(args.platform))]
    checksums = directory / f"SHA256SUMS-{args.platform}.txt"
    checksums.write_text("".join(f"{item['sha256']}  {item['name']}\n" for item in records), encoding="ascii")
    records.append(record(checksums))
    receipt = {"source_commit": args.commit, "source_tree": args.tree, "platform": args.platform,
               "run_id": os.environ.get("GITHUB_RUN_ID"), "files": records}
    if args.platform == "Ubuntu24":
        receipt["verified_linux_receipt_sha256"] = digest(ROOT / "candidate/RELEASE-Linux.json")
    write(directory / f"RELEASE-{args.platform}.json", receipt)


def verify_linux(args):
    directory = ROOT / "candidate"
    receipt = json.loads((directory / "RELEASE-Linux.json").read_text(encoding="utf-8"))
    check_receipt(receipt, args.commit, args.tree, "Linux")
    for item in receipt["files"]:
        if record(directory / item["name"]) != item:
            raise ValueError("Retained Linux bytes differ from their receipt")
    expected = (directory / "PDF-Prep").resolve()
    with tarfile.open(directory / "PDF-Prep-linux-x86_64.tar.gz") as archive:
        for member in archive.getmembers():
            if not member.name.startswith("PDF-Prep/") and member.name != "PDF-Prep":
                raise ValueError("Unexpected archive root")
            target = (directory / member.name).resolve()
            if not target.is_relative_to(expected):
                raise ValueError("Archive escapes the package")
            if member.issym() or member.islnk():
                base = target.parent if member.issym() else directory
                if not (base / member.linkname).resolve().is_relative_to(expected):
                    raise ValueError("Archive link escapes the package")
        archive.extractall(directory, filter="data")
    info = json.loads((directory / "PDF-Prep/BUILD-INFO.json").read_text(encoding="utf-8"))
    check_info(info, args.commit, args.tree, "linux")
    if info != json.loads((directory / "BUILD-INFO-Linux.json").read_text(encoding="utf-8")):
        raise ValueError("Retained package provenance differs")


def finalize(args):
    directory = ROOT / "ready"
    records = []
    receipts = {}
    for platform in PLATFORMS:
        path = directory / f"RELEASE-{platform}.json"
        receipt = json.loads(path.read_text(encoding="utf-8"))
        check_receipt(receipt, args.commit, args.tree, platform)
        receipts[platform] = receipt
        for item in receipt["files"]:
            if record(directory / item["name"]) != item:
                raise ValueError("Final artifact hash mismatch")
        records.extend([*receipt["files"], record(path)])
    if receipts["Ubuntu24"].get("verified_linux_receipt_sha256") != digest(directory / "RELEASE-Linux.json"):
        raise ValueError("Compatibility evidence does not cover the retained Linux build")
    expected = {item["name"] for item in records}
    if len(records) != len(expected) or {path.name for path in directory.iterdir()} != expected:
        raise ValueError("Missing, duplicated or unreviewed final artifacts")
    for item in source_index()["archives"]:
        if record(directory / item["name"]) != {key: item[key] for key in ("name", "size", "sha256")}:
            raise ValueError("Final matching source hash mismatch")
    write(directory / "RELEASE-READY.json", {
        "status": "automated-checks-passed-awaiting-publication-review", "tag": args.tag,
        "source_commit": args.commit, "source_tree": args.tree, "run_id": os.environ.get("GITHUB_RUN_ID"),
        "files": records, "checks": ["Windows source tests/build/packaged self-test", "Ubuntu22 source tests/build/portable and AppImage self-tests", "Ubuntu24 exact retained Linux packages", "all five matching source archives checksum-verified"],
    })


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("source", "stage", "verify-linux", "finalize"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree")
    parser.add_argument("--platform", choices=PLATFORMS)
    args = parser.parse_args(argv)
    tree = validate_source(args.tag, args.commit)
    if args.command == "source":
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"commit={args.commit}\ntree={tree}\n")
    else:
        if args.tree != tree:
            raise ValueError("Reviewed tree differs from the checkout")
        if args.command == "stage" and not args.platform:
            parser.error("Staging requires --platform")
        {"stage": stage, "verify-linux": verify_linux, "finalize": finalize}[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
