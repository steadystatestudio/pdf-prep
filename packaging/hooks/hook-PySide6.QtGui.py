"""Use Qt discovery, without unrelated PDF/keyboard/TUIO/VNC plugins.

Filtering before binary dependency analysis avoids collecting PDFium, QML,
Virtual Keyboard and networking libraries for this local QWidget application.
Linux's optional VNC display plugin otherwise pulls in QtNetwork.
Native input-method plugins remain available for ordinary text entry.
"""
from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
# The application explicitly uses Fusion; the GTK platform theme is unused.
_excluded = ("qpdf.", "qtvirtualkeyboardplugin.", "qtuiotouchplugin.", "qvnc.", "qgtk3.")
binaries = [entry for entry in binaries
            if not Path(entry[0]).name.lower().removeprefix("lib").startswith(_excluded)]
