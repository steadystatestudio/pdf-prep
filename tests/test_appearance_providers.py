"""Provider precedence, normalization, privacy and recoverable failure coverage."""
import json
import os
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QFont, QPalette

from pdf_prep import appearance

@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance() or QApplication([])
    return application


REAL_START_PORTAL = appearance._start_portal
REAL_START_NATIVE = appearance._start_native


@pytest.fixture
def pilot(app, monkeypatch, tmp_path):
    previous_font, previous_style = QFont(app.font()), app.styleSheet()
    palette = QPalette()
    palette.setColor(QPalette.Accent, QColor("#225588"))
    monkeypatch.setattr(appearance.sys, "platform", "linux")
    monkeypatch.setattr(app, "_studio_native_palette", palette, raising=False)
    monkeypatch.setattr(app, "_studio_native_font", QFont("Sans Serif", 10), raising=False)
    monkeypatch.setattr(app, "_studio_host_appearance", {}, raising=False)
    monkeypatch.setattr(app, "_studio_pilot_configured", False, raising=False)
    monkeypatch.setattr(app, "_studio_refresh_appearance", None, raising=False)
    monkeypatch.setattr(appearance, "_start_native", lambda *_: None)
    monkeypatch.setattr(appearance, "_start_portal", lambda *_: None)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "private-user-home"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "system"))
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "unknown")
    monkeypatch.delenv("QT_QPA_PLATFORMTHEME", raising=False)
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    monkeypatch.delenv("QT_SCREEN_SCALE_FACTORS", raising=False)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    yield app, settings
    app.processEvents()
    app.setStyleSheet(previous_style)
    app.setFont(previous_font)


def test_explicit_qt_config_is_read_even_without_plugin(pilot, monkeypatch, tmp_path):
    app, settings = pilot
    monkeypatch.setenv("QT_QPA_PLATFORMTHEME", "qt6ct")
    home = tmp_path / "private-user-home" / "qt6ct"
    home.mkdir(parents=True)
    config = QSettings(str(home / "qt6ct.conf"), QSettings.IniFormat)
    config.setValue("Fonts/general", QFont("Noto Sans", 17).toString())
    config.sync()
    appearance.configure_appearance(app, settings)
    assert app.font().pointSizeF() == 17
    assert app._studio_appearance_resolution["font"]["source"] == "qt6ct"
    assert app._studio_appearance_resolution["accent"]["status"] == "fallback"


def test_qt6ct_custom_palette_controls_accent_and_scheme(pilot, monkeypatch, tmp_path):
    _, _settings = pilot
    monkeypatch.setenv("QT_QPA_PLATFORMTHEME", "qt6ct")
    home = tmp_path / "private-user-home" / "qt6ct"
    home.mkdir(parents=True)
    palette = QSettings(str(home / "custom.conf"), QSettings.IniFormat)
    colors = ["#ffffff"] * QPalette.NColorRoles.value
    colors[QPalette.Window.value] = "#222222"
    colors[QPalette.WindowText.value] = "#eeeeee"
    colors[QPalette.Highlight.value] = "#dd4488"
    colors[QPalette.Accent.value] = "#dd4488"
    palette.setValue("ColorScheme/active_colors", colors)
    palette.sync()
    config = QSettings(str(home / "qt6ct.conf"), QSettings.IniFormat)
    config.setValue("Appearance/custom_palette", True)
    config.setValue("Appearance/color_scheme_path", str(home / "custom.conf"))
    config.sync()
    values = appearance.qt6ct_provider()
    assert values["accent"] == "#dd4488"
    assert values["color_scheme"] == "dark"


def test_kde_unquoted_configuration_is_read_only_for_active_desktop(pilot, monkeypatch, tmp_path):
    home = tmp_path / "private-user-home"
    home.mkdir()
    path = home / "kdeglobals"
    path.write_text("[General]\nfont=Noto Sans,15,-1,5,400,0,0,0,0,0\n"
                    "[Colors:Selection]\nBackgroundNormal=222,64,128\n"
                    "[Colors:Window]\nBackgroundNormal=32,32,32\nForegroundNormal=240,240,240\n")
    assert appearance.kde_provider()["reason"] == "inactive-desktop"
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    values = appearance.kde_provider()
    assert values["font"].pointSizeF() == 15
    assert values["accent"] == "#de4080"
    assert values["color_scheme"] == "dark"


def test_failed_accent_does_not_discard_valid_font(pilot):
    app, settings = pilot
    appearance.configure_appearance(app, settings)
    appearance._provider_finished(app, app._studio_pilot_generation, "gtk",
                                  {"font": QFont("Sans Serif", 16), "reason": "accent-unavailable"})
    assert app.font().pointSizeF() == 16
    assert app._studio_appearance_resolution["font"]["source"] == "gtk"
    assert app._studio_appearance_resolution["accent"]["source"] == "captured-qt"


def test_overrides_survive_refresh_and_reset_restores_providers(pilot):
    app, settings = pilot
    settings.setValue("appearance/font_mode", "custom")
    settings.setValue("appearance/font_family", "Sans Serif")
    settings.setValue("appearance/font_size_pt", 20.5)
    settings.setValue("appearance/accent_mode", "custom")
    settings.setValue("appearance/accent", "#e34488")
    appearance.configure_appearance(app, settings)
    appearance.configure_appearance(app, settings, refresh=True)
    assert app.font().pointSizeF() == 20.5
    assert app._studio_appearance_values["accent"] == "#e34488"
    settings.setValue("appearance/font_mode", "system")
    settings.setValue("appearance/accent_mode", "system")
    appearance.configure_appearance(app, settings)
    assert app.font().pointSizeF() == 10
    assert app._studio_appearance_values["accent"] == "#225588"


def test_windows_reset_uses_captured_values_not_previous_overrides(pilot, monkeypatch):
    app, settings = pilot
    monkeypatch.setattr(appearance.sys, "platform", "win32")
    monkeypatch.setattr(app, "_studio_captured_host_appearance",
                        {"accent": "#a94dc1", "color_scheme": "dark"}, raising=False)
    appearance.configure_appearance(app, settings)
    settings.setValue("appearance/accent_mode", "custom")
    settings.setValue("appearance/accent", "#ff4488")
    settings.setValue("theme", "light")
    appearance.configure_appearance(app, settings)
    assert app._studio_appearance_values["accent"] == "#ff4488"
    settings.setValue("appearance/accent_mode", "system")
    settings.setValue("theme", "system")
    appearance.configure_appearance(app, settings)
    assert app._studio_appearance_values["accent"] == "#a94dc1"
    assert app._studio_appearance_values["color_scheme"] == "dark"


@pytest.mark.parametrize("helper_dpi,gtk_scale,qt_dpi,qt_scale,explicit,expected", [
    (96, 1, 96, 1, None, 10),
    (144, 1, 96, 1.5, None, 10),
    (144, 1, 96, 1, None, 15),
    (96, 2, 96, 2, None, 10),
    (96, 1, 96, 1.5, "1.5", 10),
    (144, 1, 96, 2.25, "1.5", 10),
])
def test_provider_pixels_are_normalized_without_backend_assumption(
        monkeypatch, helper_dpi, gtk_scale, qt_dpi, qt_scale, explicit, expected):
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    monkeypatch.delenv("QT_SCREEN_SCALE_FACTORS", raising=False)
    if explicit:
        monkeypatch.setenv("QT_SCALE_FACTOR", explicit)
    value = {"font": {"family": "Noto Sans", "point_size": 10, "weight": 400,
                      "italic": False, "absolute_size": False},
             "font_dpi": helper_dpi, "widget_scale": gtk_scale}
    font = appearance.gtk_font(QFont(), value, qt_dpi, qt_scale)
    assert font.pointSizeF() == pytest.approx(expected)


def test_invalid_or_missing_helper_properties_remain_independent():
    screen = SimpleNamespace(logicalDotsPerInchY=lambda: 96, devicePixelRatio=lambda: 1, name=lambda: "display")
    data = {"schema": 1, "font": {"family": "Noto Sans", "point_size": 11, "absolute_size": False},
            "font_dpi": None, "widget_scale": 1, "accent": "#ff4488", "errors": ["desktop-font-dpi-unavailable"]}
    value = appearance.validate_gtk_result(data, QFont(), screen)
    assert value["font"] is None and value["accent"] == "#ff4488"
    assert appearance.validate_gtk_result({"schema": 2}, QFont(), screen)["reason"] == "invalid-helper-schema"


def test_diagnostics_distinguish_original_desktop_size_from_normalized_qt_size(pilot):
    app, settings = pilot
    screen = SimpleNamespace(logicalDotsPerInchY=lambda: 96, devicePixelRatio=lambda: 1.5, name=lambda: "display")
    data = {"schema": 1, "font": {"family": "Noto Sans", "point_size": 14, "absolute_size": False,
                                  "weight": 400, "italic": False},
            "font_dpi": 96, "widget_scale": 1, "xft_dpi": 98304, "errors": []}
    provider = appearance.validate_gtk_result(data, QFont(), screen)
    appearance.configure_appearance(app, settings)
    appearance._provider_finished(app, app._studio_pilot_generation, "gtk", provider)
    detail = json.loads(appearance.appearance_details(app))
    assert detail["scaling"]["gtk"]["requested_font"] == data["font"]
    assert detail["resolved"]["font"]["point_size"] == pytest.approx(14 / 1.5)
    for key in ("font_family", "font_size"):
        assert detail["properties"][key]["source"] == "gtk"
    data["font"]["family"] = "/home/private-person/not-a-font"
    invalid = appearance.validate_gtk_result(data, QFont(), screen)
    assert "private-person" not in json.dumps(invalid["metrics"])


@pytest.mark.parametrize("value", [(1, 0, .5), (-.01, 0, .5), (1.1, 0, 0), (True, 0, 0), None])
def test_portal_rgb_validation(value):
    result = appearance.portal_setting("accent-color", value)
    assert result == "#ff0080" if value == (1, 0, .5) else result is None


def test_portal_no_preference_and_unknown_are_not_dark():
    assert appearance.portal_setting("color-scheme", 0) is None
    assert appearance.portal_setting("color-scheme", 3) is None
    assert appearance.portal_setting("color-scheme", 1) == "dark"
    assert appearance.portal_setting("color-scheme", 2) == "light"


def test_diagnostics_exclude_config_paths_and_sensitive_environment(pilot, monkeypatch):
    app, settings = pilot
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "/home/private-person/session")
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-copy")
    appearance.configure_appearance(app, settings)
    text = appearance.appearance_details(app)
    detail = json.loads(text)
    assert "private-person" not in text and "private-user-home" not in text and "do-not-copy" not in text
    assert detail["desktop"] == "unrecognized"
    assert all(not prop["system_match_verified"] for prop in detail["properties"].values())
    assert detail["properties"]["font"]["status"] == "fallback"


@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("font_metadata", ["family", "style", "families"])
def test_pathlike_captured_font_cannot_leak_through_any_diagnostic(
        pilot, monkeypatch, tmp_path, platform, font_metadata):
    from pdf_prep import system_fonts

    app, settings = pilot
    monkeypatch.setattr(appearance.sys, "platform", platform)
    font = QFont("/home/private-person/not-a-font" if font_metadata == "family" else "Sans Serif", 14)
    if font_metadata == "style":
        font.setStyleName("/home/private-person/style")
    if font_metadata == "families":
        font.setFamilies(["Sans Serif", "/home/private-person/second-family"])
    monkeypatch.setattr(app, "_studio_native_font", font)
    monkeypatch.setattr(app, "_studio_captured_host_appearance", {}, raising=False)
    monkeypatch.setattr(system_fonts.QStandardPaths, "writableLocation", lambda *_: str(tmp_path / "local-diagnostics"))
    appearance.configure_appearance(app, settings)
    copied = appearance.appearance_details(app)
    system_fonts.accent_colors(app)
    local = (tmp_path / "local-diagnostics" / "appearance.json").read_text(encoding="utf-8")
    assert "/home/private-person" not in copied
    assert "/home/private-person" not in local
    assert "private-person" not in json.dumps(app.property("studioAppearanceDiagnostics"))
    if platform == "linux":
        assert app.font().family() == "Sans Serif"
    assert app.font().pointSizeF() == 14


def test_stale_probe_result_cannot_overwrite_refreshed_state(pilot):
    app, settings = pilot
    appearance.configure_appearance(app, settings)
    old = app._studio_pilot_generation
    appearance.configure_appearance(app, settings, refresh=True)
    appearance._provider_finished(app, old, "gtk", {"font": QFont("Sans Serif", 50)})
    assert app.font().pointSizeF() == 10


def test_portal_async_legacy_fallback_completes_without_a_host_command(pilot, monkeypatch):
    from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCall, QDBusVariant
    from PySide6.QtTest import QTest

    app, settings = pilot
    appearance.configure_appearance(app, settings)
    calls = []

    class Bus:
        def isConnected(self):
            return True

        def connect(self, *_args):
            return False

        def asyncCall(self, message, timeout):
            calls.append((message.member(), timeout))
            if message.member() == "ReadOne":
                reply = QDBusMessage.createError("org.freedesktop.DBus.Error.UnknownMethod", "unsupported")
            else:
                value = 1 if message.arguments()[1] == "color-scheme" else [1., 0., .5]
                reply = message.createReply(QDBusVariant(value))
            return QDBusPendingCall.fromCompletedCall(reply)

    monkeypatch.setattr(QDBusConnection, "sessionBus", lambda: Bus())
    # The fixture disables real queries; the real transport still consumes Qt replies.
    REAL_START_PORTAL(app, app._studio_pilot_generation)
    for _ in range(20):
        app.processEvents()
        if "portal" in app._studio_pilot_providers:
            break
        QTest.qWait(5)
    values = app._studio_pilot_providers["portal"]
    assert values["color_scheme"] == "dark" and values["accent"] == "#ff0080"
    assert calls.count(("ReadOne", 700)) == calls.count(("Read", 700)) == 2


@pytest.mark.skipif(os.name != "posix", reason="Execute native Linux QProcess failure fixtures")
@pytest.mark.parametrize("body,reason", [
    ("exec /bin/sleep 5", "helper-timeout"),
    ("kill -TERM $$", "helper-crashed"),
    ("printf '%17000s' x", "helper-response-too-large"),
    ("printf '%s' '{invalid json}'", "invalid-helper-response"),
])
def test_native_qprocess_failure_keeps_ui_usable_without_host_python(pilot, monkeypatch, tmp_path, body, reason):
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton

    app, settings = pilot
    helper = tmp_path / "native-probe-fixture"
    helper.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    helper.chmod(0o700)
    assert "python" not in helper.read_text(encoding="utf-8")
    monkeypatch.setattr(appearance, "_helper_path", lambda: helper)
    monkeypatch.setattr(appearance, "PROBE_TIMEOUT_MS", 40)
    appearance.configure_appearance(app, settings)
    generation = app._studio_pilot_generation
    appearance._provider_finished(app, generation, "portal", {"reason": "test-portal-unavailable"})
    timer = QTimer()
    timer.setInterval(5)
    heartbeat = []
    timer.timeout.connect(lambda: heartbeat.append(True))
    timer.start()
    REAL_START_NATIVE(app, generation)
    deadline = time.monotonic() + 2
    while appearance.appearance_probes_pending(app) and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    QTest.qWait(15)
    timer.stop()
    assert not appearance.appearance_probes_pending(app), "Native failure did not settle"
    assert app._studio_pilot_providers["gtk"]["reason"] == reason
    assert all(app._studio_appearance_resolution[key]["source"] == "captured-qt"
               for key in ("font", "accent", "color_scheme"))
    assert heartbeat, "GUI event loop was blocked by the helper"
    clicked = []
    button = QPushButton("Still usable")
    button.clicked.connect(lambda: clicked.append(True))
    button.click()
    assert clicked
    button.close()
