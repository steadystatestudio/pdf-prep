"""Shared UI text normalization and captured native fallback appearance.

Studio applications resolve Linux desktop preferences through independent,
bounded providers in appearance.py; host Python is never invoked.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QObject, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QTextEdit, QToolTip


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
    weights = {
        "thin": QFont.Weight.Thin,
        "ultralight": QFont.Weight.ExtraLight,
        "light": QFont.Weight.Light,
        "medium": QFont.Weight.Medium,
        "semibold": QFont.Weight.DemiBold,
        "demibold": QFont.Weight.DemiBold,
        "bold": QFont.Weight.Bold,
        "ultrabold": QFont.Weight.ExtraBold,
        "heavy": QFont.Weight.Black,
        "black": QFont.Weight.Black,
    }
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
    """Apply the resolved font without re-probing on every theme change."""
    if getattr(app, "_studio_pilot_configured", False):
        font = getattr(app, "_studio_appearance_resolved_font", None)
        if font is not None and app.font() != font:
            app.setFont(QFont(font))
        return
    if getattr(app, "_studio_system_font_initialized", False):
        return
    capture_system_appearance(app)
    if sys.platform.startswith("linux"):
        from .appearance import configure_appearance
        configure_appearance(app, None)
    else:
        app._studio_host_appearance["font_source"] = "native Qt platform font"
        app._studio_system_font_initialized = True


def system_font_css(app: QApplication) -> str:
    """Keep Qt stylesheets from resetting tables/dialogs to a native small font."""
    polish_system_text(app)
    font = app.font()
    family = font.family().replace(chr(92), chr(92) * 2).replace('"', chr(92) + '"')
    size = (
        f"{font.pointSizeF():g}pt"
        if font.pointSizeF() > 0
        else f"{font.pixelSize()}px"
        if font.pixelSize() > 0
        else None
    )
    return (
        (
            f'font-family: "{family}"; font-size: {size}; font-weight: {font.weight()}; '
            f"font-style: {'italic' if font.italic() else 'normal'};"
        )
        if size
        else ""
    )


def host_environment() -> dict[str, str]:
    """Run desktop helpers against host libraries, never bundled AppImage ones."""
    env = os.environ.copy()
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original:
        env["LD_LIBRARY_PATH"] = original
    else:
        env.pop("LD_LIBRARY_PATH", None)
    for key in ("PYTHONHOME", "PYTHONPATH", "GI_TYPELIB_PATH"):
        env.pop(key, None)
    return env


_host_lock = threading.Lock()


def _run_host(command, **kwargs):
    """Also restore the Windows DLL search path inherited by child processes."""
    kwargs["env"] = host_environment()
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return subprocess.run(command, **kwargs)
    import ctypes

    with _host_lock:
        kernel = ctypes.windll.kernel32
        buffer = ctypes.create_unicode_buffer(32768)
        kernel.GetDllDirectoryW(len(buffer), buffer)
        if not kernel.SetDllDirectoryW(None):
            raise OSError("Could not restore host DLL search path")
        try:
            return subprocess.run(command, **kwargs)
        finally:
            kernel.SetDllDirectoryW(buffer.value or None)


def _host_appearance() -> dict:
    if sys.platform.startswith("linux"):
        return {"source": "captured Qt fallback", "fallback_reason": "Desktop providers not yet resolved"}
    elif sys.platform == "win32":
        shell = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        command = [
            str(shell),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop'; "
            "$u=[Windows.UI.ViewManagement.UISettings,Windows.UI.ViewManagement,"
            "ContentType=WindowsRuntime]::new(); "
            "$c=$u.GetColorValue([Windows.UI.ViewManagement.UIColorType]::Accent); "
            "@{accent=('#{0:x2}{1:x2}{2:x2}' -f $c.R,$c.G,$c.B);"
            "text_scale=$u.TextScaleFactor} | ConvertTo-Json -Compress",
        ]
    else:
        return {"source": "native Qt platform integration"}
    try:
        result = _run_host(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        values = json.loads(result.stdout)
        if not isinstance(values, dict):
            raise ValueError("Invalid desktop appearance response")
        values["source"] = "Windows personalization" if sys.platform == "win32" else "host GTK settings"
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                    values["color_scheme"] = ("light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0]
                                              else "dark")
            except OSError:
                pass
        return values
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"source": "native Qt fallback", "fallback_reason": type(exc).__name__}


def capture_system_appearance(app: QApplication) -> dict:
    """Capture once, before Fusion/custom palettes overwrite native colors."""
    if not hasattr(app, "_studio_native_palette"):
        app._studio_native_palette = QPalette(app.palette()) if hasattr(app, "palette") else QPalette()
        app._studio_native_font = QFont(app.font())
        app._studio_host_appearance = _host_appearance()
        app._studio_captured_host_appearance = dict(app._studio_host_appearance)
    return app._studio_host_appearance


def _luminance(value: QColor) -> float:
    channels = [value.redF(), value.greenF(), value.blueF()]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def contrast_ratio(first, second) -> float:
    low, high = sorted((_luminance(QColor(first)), _luminance(QColor(second))))
    return (high + 0.05) / (low + 0.05)


def contrast_text(background) -> str:
    return max(("#000000", "#ffffff"), key=lambda color: contrast_ratio(background, color))


def accent_colors(app: QApplication) -> dict[str, str]:
    values = dict(capture_system_appearance(app))
    accent = QColor(values.get("accent") or "")
    if not accent.isValid() or accent.alpha() != 255:
        values.setdefault("fallback_reason", "Desktop integration did not expose an opaque accent")
        values["accent_source"] = "captured native Qt palette"
        accent = app._studio_native_palette.color(QPalette.ColorRole.Accent)
    if not accent.isValid() or accent.alpha() != 255:
        values["accent_source"] = "Studio fallback"
        accent = QColor("#2768ad")
    # One foreground must remain readable in all three interactive states.
    on = contrast_text(accent)
    variants = []
    for factor in (110, 120):
        variant = accent.darker(factor) if on == "#ffffff" else accent.lighter(factor)
        variants.append(variant.name())
    result = dict(accent=accent.name(), hover=variants[0], pressed=variants[1], on_accent=on)
    app.setProperty("studioAccent", result["accent"])
    app.setProperty("studioOnAccent", on)
    current_font = app.font()
    family = current_font.family()
    if not family or len(family) > 128 or any(ord(char) < 32 or char in "/\\" for char in family):
        family = "Sans Serif"
    # Never copy arbitrary style names, family lists or serialized font metadata.
    # A fresh font retains only the whitelisted appearance values we disclose.
    diagnostic_font = QFont(family)
    points, pixels = current_font.pointSizeF(), current_font.pixelSize()
    if math.isfinite(points) and 0 < points <= 10000:
        diagnostic_font.setPointSizeF(points)
    elif 0 < pixels <= 10000:
        diagnostic_font.setPixelSize(pixels)
    else:
        diagnostic_font.setPointSizeF(9)
    diagnostic_font.setWeight(current_font.weight())
    diagnostic_font.setItalic(current_font.italic())
    diagnostics = dict(values, **result, effective_font=diagnostic_font.toString(), platform=app.platformName())
    if getattr(app, "_studio_pilot_configured", False):
        from .appearance import appearance_details
        details = json.loads(appearance_details(app))
        diagnostics.update(properties=details["properties"], actual_font=details["actual_font"],
                           scaling=details["scaling"], candidate=details["version"])
    if app.property("studioAppearanceDiagnostics") != diagnostics:
        app.setProperty("studioAppearanceDiagnostics", diagnostics)
        # No filenames, documents, or other user content are recorded here.
        try:
            folder = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "appearance.json").write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
        except OSError:
            pass  # Diagnostics must never prevent the application opening.
    return result


def accent_text(app: QApplication, background: str) -> str:
    color = QColor(accent_colors(app)["accent"])
    for _ in range(80):
        if contrast_ratio(color, background) >= 4.5:
            return color.name()
        color = color.lighter(108) if _luminance(QColor(background)) < 0.4 else color.darker(108)
    return contrast_text(background)


def ui_html(text: str) -> str:
    """Normalize application prose; do not use for user documents or exports."""
    # Qt's HTML headings and <small> otherwise silently override widget fonts.
    text = re.sub(r"<h[1-6](?:\s[^>]*)?>", "<p><b>", text, flags=re.I)
    text = re.sub(r"</h[1-6]>", "</b></p>", text, flags=re.I)
    text = re.sub(r"</?(?:small|big)(?:\s[^>]*)?>", "", text, flags=re.I)
    text = re.sub(r'(<font\b[^>]*?)\s+size\s*=\s*(?:"[^"]*"|\x27[^\x27]*\x27|[^\s>]+)', r"\1", text, flags=re.I)
    text = re.sub(r"font-size\s*:[^;\"']+;?", "", text, flags=re.I)
    return text


class _WindowsAppearanceEvents(QAbstractNativeEventFilter):
    def __init__(self, watcher):
        super().__init__()
        self.watcher = watcher

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            from ctypes import wintypes

            event = wintypes.MSG.from_address(int(message))
            if event.message in (0x001A, 0x031A, 0x0320):
                self.watcher.host_timer.start(100)
        return False, 0


class _AppearanceWatcher(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.pending = set()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.refresh)
        self.font_signature = app.font().toString()
        self.refreshing = False
        app.fontChanged.connect(self.font_changed)
        self.host_timer = QTimer(self)
        self.host_timer.setSingleShot(True)
        self.host_timer.timeout.connect(self.host_changed)
        app.styleHints().colorSchemeChanged.connect(lambda *_: self.host_timer.start(100))
        if sys.platform == "win32" and app.platformName() == "windows":
            self.native_events = _WindowsAppearanceEvents(self)
            app.installNativeEventFilter(self.native_events)

    def host_changed(self):
        if sys.platform.startswith("linux"):
            from .appearance import configure_appearance
            configure_appearance(self.app, getattr(self.app, "_studio_pilot_settings", None), refresh=True)
        else:
            self.app._studio_host_appearance = _host_appearance()
            self.app._studio_captured_host_appearance = dict(self.app._studio_host_appearance)
        if sys.platform == "win32":
            # Qt's Windows platform font comes from current NONCLIENTMETRICS.
            # It already includes Windows text sizing; do not multiply DPI again.
            font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
            self.app._studio_native_font = QFont(font)
            self.app.setFont(font)
            if getattr(self.app, "_studio_pilot_configured", False):
                from .appearance import configure_appearance
                configure_appearance(self.app, getattr(self.app, "_studio_pilot_settings", None))
        self.refresh_timer.start(0)

    def font_changed(self, font):
        signature = font.toString()
        if not self.refreshing and signature != self.font_signature:
            self.font_signature = signature
            self.refresh_timer.start(0)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ThemeChange:
            self.host_timer.start(100)
        if event.type() == QEvent.Type.Show and isinstance(obj, (QLabel, QTextEdit)):
            if obj not in self.pending:
                self.pending.add(obj)
                QTimer.singleShot(0, lambda widget=obj: self.normalize(widget))
        return False

    def refresh(self):
        callback = getattr(self.app, "_studio_refresh_appearance", None)
        if callback:
            self.refreshing = True
            try:
                callback()
            finally:
                self.font_signature = self.app.font().toString()
                self.refreshing = False

    def normalize(self, widget):
        self.pending.discard(widget)
        try:
            if widget.property("studioContentTypography"):
                return
            if isinstance(widget, QTextEdit):
                widget.document().setDefaultFont(self.app.font())
                html = widget.toHtml()
                normalized = ui_html(html)
                if normalized != html:
                    widget.setHtml(normalized)
            elif isinstance(widget, QLabel) and widget.textFormat() != Qt.TextFormat.PlainText:
                text = widget.text()
                normalized = ui_html(text)
                if normalized != text:
                    widget.setText(normalized)
        except RuntimeError:
            pass  # A short-lived dialog was closed before its queued polish.


def polish_system_text(app: QApplication) -> None:
    QToolTip.setFont(app.font())
    if not hasattr(app, "_studio_appearance_watcher"):
        app._studio_appearance_watcher = _AppearanceWatcher(app)
        app.installEventFilter(app._studio_appearance_watcher)
    for widget in app.allWidgets():
        if isinstance(widget, (QLabel, QTextEdit)):
            app._studio_appearance_watcher.normalize(widget)


def bind_appearance_refresh(app: QApplication, callback) -> None:
    app._studio_refresh_appearance = callback
    polish_system_text(app)
