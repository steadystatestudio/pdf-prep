"""Preserve the pinned upstream hook, omitting its optional Mesa GL fallback.

This application paints ordinary QWidget controls and images; it does not
request OpenGL rendering. Native Qt graphics/platform libraries remain present.
"""
from pathlib import Path
import runpy

import PyInstaller

_upstream = runpy.run_path(str(Path(PyInstaller.__file__).parent / "hooks/hook-PySide6.py"))
hiddenimports = _upstream.get("hiddenimports", [])
datas = _upstream.get("datas", [])
binaries = [entry for entry in _upstream.get("binaries", [])
            if Path(entry[0]).name.lower() != "opengl32sw.dll"]
