PDF Prep: Linux x86-64

AppImage:
  chmod +x PDF-Prep-x86_64.AppImage
  ./PDF-Prep-x86_64.AppImage

If FUSE is unavailable, use extraction mode:
  APPIMAGE_EXTRACT_AND_RUN=1 ./PDF-Prep-x86_64.AppImage

Portable tar.gz:
  Extract the archive and run PDF-Prep/PDF-Prep.
  Keep _internal and the executable together. Python is bundled.

These packages are built on Ubuntu 22.04 x86-64. Consult the release receipt
for checks of the exact package bytes on Ubuntu 22.04 and 24.04. A graphical
desktop and compatible Qt/X11 or Wayland libraries are required. An AppImage
does not guarantee compatibility with every Linux distribution or hardware.
No ARM or macOS download is provided.

Ubuntu-owned libraries are supplied by your operating system, rather than
copied into these packages. Install the packages named by the install command
in LINUX-SYSTEM-DEPENDENCIES.json. Ubuntu 24.04 may resolve package names to
their t64 replacements. The package manager supplies their updates, notices
and source availability. Python, Qt/PySide, Pillow, pypdf and PDFium remain
bundled; a separate Python installation is not needed to run this app.

Verify SHA256SUMS-Linux.txt from the same release before running a package.
AppImages are unsigned. To inspect the AppImage without installing it:
  ./PDF-Prep-x86_64.AppImage --appimage-extract

The separate shared libraries may be replaced with compatible modified
versions in the extracted application folder. See THIRD-PARTY.md,
SOURCE-INDEX.json and licenses/ for matching library sources, complete notices
and replacement/rebuild instructions. Document processing stays local.
