"""Independent, bounded desktop appearance providers for Studio desktop applications.

Linux probes run through bundled QtDBus and an isolated native helper. No host
Python, PyGObject, settings command, or network service is needed by this module.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, QTimer, qVersion
from PySide6.QtGui import QColor, QFont, QFontInfo, QPalette

MAX_CONFIG_BYTES = 131072
MAX_HELPER_BYTES = 16384
PROBE_TIMEOUT_MS = 2000


def _number(value, minimum=0.0, maximum=10000.0):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and minimum < value <= maximum else None


def _safe_text(value, limit=128):
    if not isinstance(value, str) or not value or len(value) > limit:
        return None
    return value if not any(ord(c) < 32 for c in value) else None


def _font_family(value):
    value = _safe_text(value)
    return value if value and "/" not in value and "\\" not in value else None


def _environment_label(key):
    value = os.environ.get(key, "")
    return value[:96] if re.fullmatch(r"[A-Za-z0-9_.:+ -]*", value) else "unrecognized"


def _color(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", value):
        return None
    result = QColor(value)
    return result.name() if result.isValid() and result.alpha() == 255 else None


def _brightness(color):
    color = QColor(color)
    return .2126 * color.redF() + .7152 * color.greenF() + .0722 * color.blueF()


def _scheme(background, foreground):
    background, foreground = _color(background), _color(foreground)
    if background and foreground and abs(_brightness(background) - _brightness(foreground)) > .15:
        return "dark" if _brightness(background) < _brightness(foreground) else "light"
    return None


def _read_ini(path):
    # QSettings correctly handles Qt font serialization and QStringList palettes.
    try:
        if not path.is_file():
            return None, "configuration-unavailable"
        if path.stat().st_size > MAX_CONFIG_BYTES:
            return None, "configuration-too-large"
        settings = QSettings(str(path), QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        settings.allKeys()
        if settings.status() != QSettings.Status.NoError:
            return None, "invalid-configuration"
        return settings, None
    except OSError:
        return None, "configuration-unreadable"


def _serialized_font(value):
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        value = ",".join(value)
    if not _safe_text(value, 512) or "," not in value:
        return None
    font = QFont()
    if not font.fromString(value) or not _font_family(font.family()):
        return None
    if _number(font.pointSizeF(), maximum=256) or _number(font.pixelSize(), maximum=1024):
        return font
    return None


def _config_roots():
    home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return [home, *(Path(p) for p in os.environ.get("XDG_CONFIG_DIRS", "/etc/xdg").split(":") if p)]


def _find_config(relative):
    for root in _config_roots():
        path = root / relative
        if path.is_file():
            return path
    return _config_roots()[0] / relative


def qt6ct_provider():
    selected = os.environ.get("QT_QPA_PLATFORMTHEME", "").lower().split(":")
    if "qt6ct" not in selected:
        return {"reason": "not-explicitly-selected"}
    path = _find_config("qt6ct/qt6ct.conf")
    settings, error = _read_ini(path)
    if error:
        return {"reason": error}
    result = {}
    font = _serialized_font(settings.value("Fonts/general"))
    if font is not None:
        result["font"] = font
    custom = str(settings.value("Appearance/custom_palette", "false")).lower() in {"true", "1"}
    if custom:
        name = settings.value("Appearance/color_scheme_path", "")
        if _safe_text(name, 4096):
            # Expanding a configuration's path is read-only; never include it in diagnostics.
            palette_path = Path(os.path.expandvars(os.path.expanduser(name)))
            if not palette_path.is_absolute():
                palette_path = path.parent / palette_path
            palette, error = _read_ini(palette_path)
            if not error:
                active = palette.value("ColorScheme/active_colors", [])
                if isinstance(active, str):
                    active = active.split(",")
                if isinstance(active, list) and len(active) >= QPalette.ColorRole.Accent.value:
                    accent_index = QPalette.ColorRole.Accent.value
                    accent = _color(active[accent_index]) if len(active) > accent_index else None
                    result["accent"] = accent or _color(active[QPalette.ColorRole.Highlight.value])
                    result["color_scheme"] = _scheme(active[QPalette.ColorRole.Window.value],
                                                     active[QPalette.ColorRole.WindowText.value])
            else:
                result["reason"] = "palette-" + error
    if not any(result.get(key) is not None for key in ("font", "accent", "color_scheme")):
        result.setdefault("reason", "no-valid-supported-values")
    return result


def _kde_color(value):
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        value = ",".join(value)
    if not isinstance(value, str):
        return None
    if _color(value):
        return _color(value)
    try:
        values = [int(part) for part in value.split(",")]
    except ValueError:
        return None
    if len(values) == 3 and all(0 <= value <= 255 for value in values):
        return QColor(*values).name()
    return None


def kde_provider():
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" +
               os.environ.get("XDG_SESSION_DESKTOP", "")).lower()
    if not any(token in desktop.split(":") for token in ("kde", "plasma")):
        return {"reason": "inactive-desktop"}
    # Merge generic configuration from lowest to highest precedence, as KDE does.
    values = {}
    for root in reversed(_config_roots()):
        settings, error = _read_ini(root / "kdeglobals")
        if not error:
            for key in ("General/font", "General/AccentColor", "Colors:Selection/BackgroundNormal",
                        "Colors:Window/BackgroundNormal", "Colors:Window/ForegroundNormal"):
                # QSettings treats the INI [General] group as its root group.
                ini_key = key.removeprefix("General/") if key.startswith("General/") else key
                if settings.contains(ini_key):
                    values[key] = settings.value(ini_key)
    result = {}
    font = _serialized_font(values.get("General/font"))
    if font is not None:
        result["font"] = font
    result["accent"] = (_kde_color(values.get("General/AccentColor")) or
                        _kde_color(values.get("Colors:Selection/BackgroundNormal")))
    result["color_scheme"] = _scheme(_kde_color(values.get("Colors:Window/BackgroundNormal")),
                                     _kde_color(values.get("Colors:Window/ForegroundNormal")))
    if not any(result.get(key) is not None for key in ("font", "accent", "color_scheme")):
        result["reason"] = "no-valid-supported-values"
    return result


def explicit_display_scale(screen_name=""):
    """Preserve intentional Qt display overrides while normalizing GTK pixels."""
    factor = _number(os.environ.get("QT_SCALE_FACTOR", 1), maximum=16) or 1.0
    entries = os.environ.get("QT_SCREEN_SCALE_FACTORS", "")
    if entries:
        selected = None
        plain = []
        for entry in entries.split(";"):
            if "=" in entry:
                name, value = entry.rsplit("=", 1)
                if name == screen_name:
                    selected = _number(value, maximum=16)
            else:
                plain.append(_number(entry, maximum=16))
        if selected is None and len(plain) == 1:
            selected = plain[0]
        factor *= selected or 1.0
    return factor


def gtk_font(native, data, logical_dpi, device_scale, screen_name=""):
    """Convert GTK's effective physical font size to Qt's point-size space."""
    description = data.get("font")
    if not isinstance(description, dict):
        return None
    family = _font_family(description.get("family"))
    size = _number(description.get("point_size"), maximum=256)
    dpi = _number(data.get("font_dpi"), maximum=1536)
    scale = _number(data.get("widget_scale"), maximum=16)
    qt_dpi = _number(logical_dpi, maximum=1536)
    qt_scale = _number(device_scale, maximum=32)
    if not all((family, size, dpi, scale, qt_dpi, qt_scale)):
        return None
    absolute = description.get("absolute_size")
    if not isinstance(absolute, bool):
        return None
    physical_pixels = size * scale * (1 if absolute else dpi / 72)
    points = physical_pixels * explicit_display_scale(screen_name) * 72 / (qt_dpi * qt_scale)
    if not _number(points, maximum=256):
        return None
    font = QFont(native)
    font.setFamily(family)
    font.setPointSizeF(points)
    weight = _number(description.get("weight"), maximum=1000)
    if weight is not None:
        font.setWeight(QFont.Weight(round(weight)))
    font.setItalic(description.get("italic") is True)
    return font


def validate_gtk_result(data, native, screen):
    if not isinstance(data, dict) or data.get("schema") != 1:
        return {"reason": "invalid-helper-schema"}
    result = {"font": gtk_font(native, data, screen.logicalDotsPerInchY(),
                               screen.devicePixelRatio(), screen.name()) if screen else None,
              "accent": _color(data.get("accent")),
              "color_scheme": data.get("color_scheme") if data.get("color_scheme") in {"dark", "light"} else None}
    # Numeric GTK evidence is useful for determining where scaling was applied.
    result["metrics"] = {key: data.get(key) for key in ("font_dpi", "widget_scale", "xft_dpi")
                         if _number(data.get(key), maximum=2000000) is not None}
    original = data.get("font")
    if isinstance(original, dict) and _font_family(original.get("family")):
        requested = {"family": original["family"]}
        for key, maximum in (("point_size", 256), ("weight", 1000)):
            value = _number(original.get(key), maximum=maximum)
            if value is not None:
                requested[key] = value
        for key in ("italic", "absolute_size"):
            if isinstance(original.get(key), bool):
                requested[key] = original[key]
        result["metrics"]["requested_font"] = requested
    if not any(result.get(key) is not None for key in ("font", "accent", "color_scheme")):
        result["reason"] = "no-valid-supported-values"
    errors = data.get("errors", [])
    result["errors"] = [error for error in errors[:12] if isinstance(error, str) and
                        re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", error)] if isinstance(errors, list) else []
    return result


def _setting(settings, key, default):
    return settings.value(key, default) if settings is not None else default


def _overrides(settings, native, theme_key="theme"):
    result = {}
    if _setting(settings, "appearance/font_mode", "system") == "custom":
        family = _font_family(_setting(settings, "appearance/font_family", ""))
        size = _number(_setting(settings, "appearance/font_size_pt", 0), 5.999, 72)
        if family and size:
            font = QFont(native)
            font.setFamily(family)
            font.setPointSizeF(size)
            result["font"] = font
        else:
            result["reason"] = "invalid-font-override"
    if _setting(settings, "appearance/accent_mode", "system") == "custom":
        result["accent"] = _color(_setting(settings, "appearance/accent", ""))
        if result["accent"] is None:
            result["reason"] = "invalid-accent-override"
    if _setting(settings, theme_key, "system") in {"dark", "light"}:
        result["color_scheme"] = _setting(settings, theme_key, "system")
    return result


def _fallback(app):
    font = QFont(app._studio_native_font)
    if not _font_family(font.family()):
        font.setFamily("Sans Serif")
    if not _number(font.pointSizeF(), maximum=256) and not _number(font.pixelSize(), maximum=1024):
        font.setPointSizeF(9)
    palette = app._studio_native_palette
    accent = _color(palette.color(QPalette.ColorRole.Accent).name(QColor.NameFormat.HexArgb))
    scheme = _scheme(palette.color(QPalette.ColorRole.Window).name(),
                     palette.color(QPalette.ColorRole.WindowText).name())
    return {"font": font, "accent": accent, "color_scheme": scheme}


def _resolve(app):
    settings = getattr(app, "_studio_pilot_settings", None)
    theme_key = getattr(app, "_studio_appearance_theme_key", "theme")
    providers = [("app-override", _overrides(settings, app._studio_native_font, theme_key))]
    if sys.platform.startswith("linux"):
        cache = app._studio_pilot_providers
        providers += [(name, cache.get(name, {"reason": "pending"}))
                      for name in ("qt6ct", "portal", "kde", "gtk")]
    else:
        host = getattr(app, "_studio_captured_host_appearance", app._studio_host_appearance)
        providers.append(("native-platform", dict(host, font=QFont(app._studio_native_font))))
    providers.append(("captured-qt", _fallback(app)))
    providers.append(("application-default", {"font": QFont("Sans Serif", 9), "accent": "#2768ad",
                                               "color_scheme": "light"}))
    chosen = {}
    records = {}
    for key in ("font", "accent", "color_scheme"):
        attempts = []
        for name, values in providers:
            value = values.get(key)
            if value is not None and key not in chosen:
                chosen[key] = value
                status = ("override" if name == "app-override" else "fallback" if name in
                          {"captured-qt", "application-default"} else "provider")
                records[key] = {"source": name, "status": status,
                                "system_match_verified": False}
            attempt = {"provider": name, "result": "available" if value is not None else
                       values.get("reason", "property-unavailable")}
            errors = values.get("errors", [])
            if errors:
                attempt["details"] = errors
            attempts.append(attempt)
        records[key]["attempts"] = attempts
    font = QFont(chosen["font"])
    # Family and size share a validated font candidate, but their provenance and
    # effective values remain separately visible for scaling diagnosis.
    records["font_family"] = dict(records["font"], value=_font_family(font.family()) or "unavailable")
    records["font_size"] = dict(records["font"], value=font.pointSizeF(), unit="pt")
    app._studio_appearance_resolved_font = font
    app._studio_appearance_resolution = records
    app._studio_appearance_values = chosen
    app._studio_host_appearance.update(accent=chosen["accent"], color_scheme=chosen["color_scheme"],
                                      source="per-property appearance providers",
                                      font_source=records["font"]["source"],
                                      accent_source=records["accent"]["source"])
    if records["font"]["status"] != "fallback" and records["accent"]["status"] != "fallback":
        app._studio_host_appearance.pop("fallback_reason", None)
    # QColor/Qt signals can produce another polish; avoid resetting identical fonts.
    if app.font() != font:
        app.setFont(font)


def _provider_finished(app, generation, name, values):
    if generation != app._studio_pilot_generation:
        return
    app._studio_pilot_providers[name] = values
    _resolve(app)
    callback = getattr(app, "_studio_refresh_appearance", None)
    if callback:
        callback()


def _helper_path():
    packaged = Path(__file__).resolve().parent / "assets/appearance-probe"
    return packaged if packaged.is_file() else Path(__file__).resolve().parents[2] / "build/native/appearance-probe"


def _start_native(app, generation):
    from .system_fonts import host_environment
    helper = _helper_path()
    if not helper.is_file():
        _provider_finished(app, generation, "gtk", {"reason": "native-helper-unavailable"})
        return
    process = QProcess(app)
    timer = QTimer(process)
    timer.setSingleShot(True)
    environment = QProcessEnvironment()
    for key, value in host_environment().items():
        environment.insert(key, value)
    process.setProcessEnvironment(environment)
    process.setStandardErrorFile(os.devnull)
    process.setProgram(str(helper))
    state = {"buffer": bytearray(), "done": False}
    app._studio_pilot_async.append(process)

    def complete(reason=None):
        if state["done"]:
            return
        state["done"] = True
        timer.stop()
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
        result = {"reason": reason} if reason else None
        if result is None:
            try:
                data = json.loads(bytes(state["buffer"]))
                result = validate_gtk_result(data, app._studio_native_font, app.primaryScreen())
            except (ValueError, UnicodeError, TypeError):
                result = {"reason": "invalid-helper-response"}
        _provider_finished(app, generation, "gtk", result)
        process.deleteLater()

    def read():
        if state["done"]:
            return
        state["buffer"].extend(bytes(process.read(MAX_HELPER_BYTES + 1 - len(state["buffer"]))))
        if len(state["buffer"]) > MAX_HELPER_BYTES:
            state["buffer"] = state["buffer"][:MAX_HELPER_BYTES]
            complete("helper-response-too-large")

    def finished(code, status):
        read()
        complete(None if code == 0 and status == QProcess.ExitStatus.NormalExit else "helper-failed")

    def failed(error):
        reason = ("helper-crashed" if error == QProcess.ProcessError.Crashed else
                  "helper-process-unavailable" if error == QProcess.ProcessError.FailedToStart else
                  "helper-process-error")
        complete(reason)

    process.readyReadStandardOutput.connect(read)
    process.finished.connect(finished)
    process.errorOccurred.connect(failed)
    timer.timeout.connect(lambda: complete("helper-timeout"))
    timer.start(PROBE_TIMEOUT_MS)
    process.start()


def _portal_value(value):
    # Qt automatically unmarshals most variants; retain support for explicit wrappers.
    for _ in range(4):
        if hasattr(value, "variant"):
            value = value.variant()
        else:
            break
    return value


def portal_setting(key, value):
    value = _portal_value(value)
    # A D-Bus RGB structure remains a QDBusArgument on some PySide versions.
    if key == "accent-color" and hasattr(value, "currentSignature"):
        try:
            if value.currentSignature() == "(ddd)":
                value.beginStructure()
                rgb = [value.asVariant() for _ in range(3)]
                value.endStructure()
                value = rgb
        except (RuntimeError, TypeError):
            return None
    if key == "color-scheme" and isinstance(value, int) and not isinstance(value, bool):
        return {1: "dark", 2: "light"}.get(value)
    if key == "accent-color" and isinstance(value, (tuple, list)) and len(value) == 3:
        rgb = [_number(channel, minimum=-.000001, maximum=1) for channel in value]
        if all(channel is not None for channel in rgb):
            return QColor.fromRgbF(*rgb).name()
    return None


def _start_portal(app, generation):
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher, QDBusVariant
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            raise RuntimeError
    except (ImportError, RuntimeError):
        _provider_finished(app, generation, "portal", {"reason": "session-portal-unavailable"})
        return
    pending = {"color-scheme", "accent-color"}
    result = {}
    failures = []

    def request(key, legacy=False):
        message = QDBusMessage.createMethodCall("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                                               "org.freedesktop.portal.Settings", "Read" if legacy else "ReadOne")
        message.setArguments(["org.freedesktop.appearance", key])
        watcher = QDBusPendingCallWatcher(bus.asyncCall(message, 700), app)
        app._studio_pilot_async.append(watcher)

        def finished(completed):
            reply = completed.reply()
            if reply.type() == QDBusMessage.MessageType.ErrorMessage:
                if not legacy and reply.errorName() in {"org.freedesktop.DBus.Error.UnknownMethod",
                                                         "org.freedesktop.portal.Error.NotFound"}:
                    request(key, True)
                    watcher.deleteLater()
                    return
                failures.append("portal-property-unavailable")
            else:
                arguments = reply.arguments()
                value = arguments[0] if arguments else None
                result["color_scheme" if key == "color-scheme" else "accent"] = portal_setting(key, value)
            pending.discard(key)
            if not pending:
                if not any(result.get(key) for key in ("accent", "color_scheme")):
                    result["reason"] = "portal-values-unavailable"
                result["errors"] = sorted(set(failures))
                _provider_finished(app, generation, "portal", result)
            watcher.deleteLater()
        watcher.finished.connect(finished)

    request("color-scheme")
    request("accent-color")
    # Subscriptions are app-owned and should only be installed once.
    if not getattr(app, "_studio_portal_subscription", False):
        # QtDBus requires a QObject slot for signals; a lightweight observer owns it.
        from PySide6.QtCore import SLOT, QObject, Slot

        class PortalObserver(QObject):
            @Slot(str, str, QDBusVariant)
            def changed(self, namespace, key, _value):
                if namespace == "org.freedesktop.appearance" and key in {"color-scheme", "accent-color"}:
                    configure_appearance(app, getattr(app, "_studio_pilot_settings", None), refresh=True)

        observer = PortalObserver(app)
        try:
            connected = bus.connect("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                                    "org.freedesktop.portal.Settings", "SettingChanged", observer,
                                    SLOT("changed(QString,QString,QDBusVariant)"))
        except (TypeError, RuntimeError):
            connected = False
        app._studio_portal_subscription = bool(connected)
        app._studio_portal_observer = observer


def configure_appearance(app, settings, refresh=False, theme_key=None):
    """Apply saved overrides immediately; complete desktop probes asynchronously."""
    from .system_fonts import capture_system_appearance
    capture_system_appearance(app)
    if theme_key is not None:
        app._studio_appearance_theme_key = theme_key
    app._studio_pilot_settings = settings
    new = not getattr(app, "_studio_pilot_configured", False)
    app._studio_pilot_configured = True
    if new or refresh:
        app._studio_pilot_generation = getattr(app, "_studio_pilot_generation", 0) + 1
        app._studio_pilot_async = []
        app._studio_pilot_providers = {"qt6ct": qt6ct_provider(), "kde": kde_provider()}
    _resolve(app)
    if sys.platform.startswith("linux") and (new or refresh):
        generation = app._studio_pilot_generation
        # Let the caller install its theme and show the window before any I/O.
        QTimer.singleShot(0, lambda: _start_native(app, generation))
        QTimer.singleShot(0, lambda: _start_portal(app, generation))
    app._studio_system_font_initialized = True


def resolved_color_scheme(app):
    return getattr(app, "_studio_appearance_values", {}).get("color_scheme")


def appearance_probes_pending(app):
    if not sys.platform.startswith("linux"):
        return False
    providers = getattr(app, "_studio_pilot_providers", {})
    return "gtk" not in providers or "portal" not in providers


def appearance_details(app):
    """Copyable, content-free diagnostics; never serialize paths or raw exceptions."""
    from . import __version__
    font = app.font()
    actual = QFontInfo(font)
    screen = app.primaryScreen()
    values = getattr(app, "_studio_appearance_values", {})
    resolution = getattr(app, "_studio_appearance_resolution", {})
    requested = {"family": font.family(), "point_size": font.pointSizeF(), "pixel_size": font.pixelSize()}
    # Family names are normally ordinary text; do not copy path-like invalid values.
    requested["family"] = _font_family(requested["family"]) or "unavailable"
    source_identity = {}
    if getattr(sys, "frozen", False):
        try:
            path = Path(sys.executable).parent / "BUILD-INFO.json"
            if path.stat().st_size <= MAX_CONFIG_BYTES:
                build_info = json.loads(path.read_text(encoding="utf-8"))
                for key in ("source_commit", "source_tree"):
                    value = build_info.get(key)
                    if isinstance(value, str) and re.fullmatch("[a-f0-9]{40}", value):
                        source_identity[key] = value
        except (OSError, ValueError, TypeError):
            pass
    detail = {"schema": 1, "application": app.applicationDisplayName() or app.applicationName(), "version": __version__,
              "build": source_identity,
              "qt_version": qVersion(), "desktop": _environment_label("XDG_CURRENT_DESKTOP"),
              "session": _environment_label("XDG_SESSION_TYPE"), "platform": app.platformName(),
              "requested_qt_integration": _environment_label("QT_QPA_PLATFORMTHEME"),
              "resolved": {"font": requested, "accent": values.get("accent"),
                           "color_scheme": values.get("color_scheme")},
              "actual_font": {"family": _font_family(actual.family()) or "unavailable",
                              "point_size": actual.pointSizeF(),
                              "pixel_size": actual.pixelSize(), "exact_match": actual.exactMatch()},
              "properties": resolution,
              "scaling": {"qt_logical_dpi": screen.logicalDotsPerInchY() if screen else None,
                          "qt_device_scale": screen.devicePixelRatio() if screen else None,
                          "explicit_qt_display_scale": explicit_display_scale(screen.name() if screen else ""),
                          "gtk": getattr(app, "_studio_pilot_providers", {}).get("gtk", {}).get("metrics", {})},
              "portal_change_notifications": bool(getattr(app, "_studio_portal_subscription", False)),
              "refresh": "Use Refresh system settings after desktop changes if the session sends no notification.",
              "privacy": "Local appearance values only; no documents, filenames, home paths or telemetry."}
    return json.dumps(detail, indent=2, ensure_ascii=False)
