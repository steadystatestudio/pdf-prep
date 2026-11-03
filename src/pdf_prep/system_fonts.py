"""Use the desktop UI font, including GTK desktop settings in bundled Qt apps.

Qt owns display scaling. X11 also supplies GTK text scaling through Xft DPI,
which Qt incorporates into its DPI/device scale. Only Wayland needs the GTK
text factor applied explicitly; applying it again on X11 would double it.
Desktop font changes are picked up when the app is reopened.
"""
from __future__ import annotations

import ast
import math
import os
import re
import subprocess
import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


def _setting(schema: str, key: str):
    try:
        result = subprocess.run(
            ["gsettings", "get", schema, key], capture_output=True, text=True,
            timeout=1, check=True,
        )
        return ast.literal_eval(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError, SyntaxError):
        return None


def _desktop_schema() -> str | None:
    # Respect a deliberately selected Qt settings integration (qt6ct, KDE, ...).
    theme = os.environ.get("QT_QPA_PLATFORMTHEME", "").lower()
    if theme and theme not in {"gtk3", "gtk2", "gnome"}:
        return None
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" +
               os.environ.get("XDG_SESSION_DESKTOP", "")).lower()
    if "cinnamon" in desktop:
        return "org.cinnamon.desktop.interface"
    if "gnome" in desktop or "ubuntu" in desktop:
        return "org.gnome.desktop.interface"
    if "mate" in desktop:
        return "org.mate.interface"
    return None


def desktop_font(native: QFont, description: str, scale: float, logical_dpi: float) -> QFont:
    """Resolve a Pango desktop font description, falling back on invalid values."""
    match = re.fullmatch(r"\s*(.+?)\s+(\d+(?:\.\d+)?)\s*", description)
    if not match or not all(math.isfinite(v) and v > 0 for v in (scale, logical_dpi)):
        return QFont(native)
    size = float(match[2])
    if not math.isfinite(size) or size <= 0:
        return QFont(native)
    words = match[1].split()
    weight = QFont.Weight.Normal
    italic = False
    weights = {"thin": QFont.Weight.Thin, "ultralight": QFont.Weight.ExtraLight,
               "light": QFont.Weight.Light, "medium": QFont.Weight.Medium,
               "semibold": QFont.Weight.DemiBold, "demibold": QFont.Weight.DemiBold,
               "bold": QFont.Weight.Bold, "ultrabold": QFont.Weight.ExtraBold,
               "heavy": QFont.Weight.Black, "black": QFont.Weight.Black}
    while len(words) > 1:
        style = words[-1].lower().replace("-", "")
        if style in {"italic", "oblique"}:
            italic = True
        elif style in weights:
            weight = weights[style]
        elif style not in {"regular", "normal", "book", "roman"}:
            break
        words.pop()
    font = QFont(native)
    font.setFamily(" ".join(words))
    font.setWeight(weight)
    font.setItalic(italic)
    font.setPointSizeF(size * scale * 96.0 / logical_dpi)
    return font


def apply_system_font(app: QApplication) -> None:
    """Initialize once; appearance changes must never reset or shrink the font."""
    if getattr(app, "_studio_system_font_initialized", False):
        return
    app._studio_system_font_initialized = True
    if not sys.platform.startswith("linux"):
        return
    schema = _desktop_schema()
    if schema is None:
        return
    description = _setting(schema, "font-name")
    if not isinstance(description, str):
        return
    scale = _setting(schema, "text-scaling-factor")
    if not isinstance(scale, (float, int)) or isinstance(scale, bool) or not math.isfinite(scale) or scale <= 0:
        scale = 1.0
    screen = app.primaryScreen()
    dpi = screen.logicalDotsPerInchY() if screen else 96.0
    if app.platformName() == "xcb":
        # Xft DPI already carries text scaling into Qt's logical DPI or DPR.
        # Retain the desktop point size; explicit QT_SCALE_FACTOR also remains
        # Qt's responsibility and must not be cancelled here.
        scale, dpi = 1.0, 96.0
    app.setFont(desktop_font(app.font(), description, scale, dpi))


def system_font_css(app: QApplication) -> str:
    """Keep Qt stylesheets from resetting tables/dialogs to a native small font."""
    font = app.font()
    family = font.family().replace(chr(92), chr(92) * 2).replace('"', chr(92) + '"')
    size = (f"{font.pointSizeF():g}pt" if font.pointSizeF() > 0
            else f"{font.pixelSize()}px" if font.pixelSize() > 0 else None)
    return f'font-family: "{family}"; font-size: {size};' if size else ""
