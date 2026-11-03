# PDF Prep

Combine PDFs and photos, put the pages in order, and prepare a new PDF for an upload. A free, offline Steady State Studio desktop tool.

- Add PDFs, JPEGs and PNGs. Inspect page thumbnails and source filenames.
- Reorder, rotate, remove, or extract selected pages.
- Prepare the result and inspect the generated PDF and its actual size before saving.
- Choose a maximum size in decimal MB (1 MB = 1,000,000 bytes). The app reports when it cannot meet the limit.
- Save a new PDF. Original files and existing destinations are never overwritten.
- System, Light and Dark appearance; keyboard controls; optional donations.

No accounts, document uploads, telemetry, imposed watermarks or paid features. Preparing a preview writes no output file. Preferences store appearance and the last maximum-size choice, not document contents or recent paths.

## Windows download

Download the portable ZIP from [GitHub Releases](https://github.com/steadystatestudio/pdf-prep/releases), extract it, and run `PDF-Prep/PDF-Prep.exe`. Keep the `_internal` folder beside the executable. Python is bundled. The package is unsigned; Windows may display an unknown-publisher warning. Check its SHA-256 against the release checksum and follow your organisation's policy.

Windows packages target x64. Linux x86_64 packages are built on Ubuntu 22.04; the Linux release receipt records checks of the same package bytes on Ubuntu 22.04 and 24.04. macOS and ARM packages are not provided. Broad hardware, high-DPI, screen-reader and real-user task testing remain ongoing.

## Linux download

The [version 1.0.2 release](https://github.com/steadystatestudio/pdf-prep/releases/tag/v1.0.2) provides `PDF-Prep-x86_64.AppImage` and `PDF-Prep-linux-x86_64.tar.gz`. Compare the download with `SHA256SUMS-Linux.txt`. Python is bundled in both formats. A graphical desktop and the ordinary Qt/X11 or Wayland runtime libraries are required; see the package's `README-LINUX.txt` and `LINUX-SYSTEM-DEPENDENCIES.json` for the recorded prerequisites. The release receipt identifies the exact source for both platform builds.

For AppImage, make the file executable and run it. Where FUSE is unavailable, run `APPIMAGE_EXTRACT_AND_RUN=1 ./PDF-Prep-x86_64.AppImage`. For the portable archive, extract it and run `PDF-Prep/PDF-Prep`, keeping `_internal` beside the executable. Saving uses a no-replace hard link on Linux. Choose a local destination filesystem that supports hard links; unsupported destinations fail without overwriting files.

## Important document limits

Original-quality preparation preserves PDF page content and searchable text where possible. This is page assembly, not a faithful archival rewrite of all document-level features. PDF portfolios, attachments, bookmarks, document metadata and advanced interactive behavior are outside this version's scope. Encrypted PDFs and PDFs containing forms or signatures are rejected; use an unsigned, flattened copy exported by the authoring application.

If original-quality output exceeds the chosen limit, fit-to-limit preparation can turn pages into JPEG images. The app labels that result before saving. Text search, selectable text, links, layers and interactive features are lost in image output. Inspect every page at full preview size and confirm readability. Compression stops at a defined quality floor; it cannot guarantee that every document will fit. Change the target or choose original quality if the target cannot be reached.

Input limits: 1,000 pages per job, 256 MiB per file, 512 MiB of combined source files and 40 megapixels per image. These input-memory limits use binary MiB; the chosen output limit uses decimal MB. Animated PNGs and PDFs with invalid or extreme page dimensions are unsupported. Rendering and compression can use significant memory. Only `.pdf`, `.jpg`, `.jpeg` and `.png` inputs are supported. If an original changes after import, remove it from the plan and add it again before preparing the result.

## Run and build from source

Use Python 3.12+ on Windows x64:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test,build]"
.venv\Scripts\python -m pdf_prep
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts\build.py
```

The one-folder build keeps Qt libraries replaceable. See `THIRD-PARTY.md` and the complete bundled license notices. The release receipt records checks of the exact package bytes, rather than implying universal compatibility.

On Linux x86_64, use Python 3.12+ in a virtual environment and install the ordinary Qt desktop dependencies for your distribution:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-build.txt
python -m pip install -e '.[test]'
python -m pdf_prep
QT_QPA_PLATFORM=offscreen python -m pytest -q
python scripts/fetch_appimagetool.py
python scripts/build.py
```

The build produces a portable archive and an AppImage. `python scripts/build.py --bundle-only` omits the AppImage. The Linux workflow builds on Ubuntu 22.04 and exercises the retained packages on Ubuntu 24.04, including graphical controls under Xvfb. This does not certify every distribution, display server or desktop environment.

[Studio website](https://steady-state-studio.com/) · [Ideas and feedback](https://steady-state-studio.com/contact/) · [Optional support](https://ko-fi.com/steadystatestudio)


Native packages are built and tested privately from the release's published source commit. Public releases retain the package checksums, build and platform receipts, matching dependency sources and synthetic QA; private build logs are not linked as public evidence.
