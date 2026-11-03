from PySide6.QtGui import QFont
import pytest
from pdf_prep import system_fonts


@pytest.mark.parametrize("dpi,scale,expected", [(96, 1, 11), (96, 1.5, 16.5), (144, 1.5, 11)])
def test_desktop_font_tracks_text_scale_without_double_scaling(dpi, scale, expected):
    result = system_fonts.desktop_font(QFont("Sans Serif", 9), "Noto Sans 11", scale, dpi)
    assert result.family() == "Noto Sans"
    assert result.pointSizeF() == pytest.approx(expected)


def test_desktop_font_preserves_style_and_multiword_family():
    result = system_fonts.desktop_font(QFont(), "Noto Sans Bold Italic 13.5", 1, 96)
    assert result.family() == "Noto Sans"
    assert result.bold() and result.italic()
    assert result.pointSizeF() == 13.5


@pytest.mark.parametrize("description,scale", [("invalid", 1), ("Sans 0", 1), ("Sans 12", float("nan"))])
def test_invalid_desktop_setting_preserves_native_font(description, scale):
    native = QFont("Native UI", 14)
    assert system_fonts.desktop_font(native, description, scale, 96) == native


def test_explicit_qt_settings_integration_is_respected(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "X-Cinnamon")
    monkeypatch.setenv("QT_QPA_PLATFORMTHEME", "qt6ct")
    assert system_fonts._desktop_schema() is None


def test_missing_gsettings_is_safe(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(system_fonts.subprocess, "run", missing)
    assert system_fonts._setting("org.cinnamon.desktop.interface", "font-name") is None


@pytest.mark.parametrize("platform,dpr,points", [("xcb", 1.5, 14), ("wayland", 1.0, 21)])
def test_x11_text_scaling_is_not_applied_twice(monkeypatch, platform, dpr, points):
    from types import SimpleNamespace
    from unittest.mock import Mock
    app = Mock(spec=["font", "setFont", "primaryScreen", "platformName"])
    app.font.return_value = QFont("Liberation Sans", 14)
    app.platformName.return_value = platform
    app.primaryScreen.return_value = SimpleNamespace(
        logicalDotsPerInchY=lambda: 96, devicePixelRatio=lambda: dpr)
    monkeypatch.setattr(system_fonts.sys, "platform", "linux")
    monkeypatch.setattr(system_fonts, "_desktop_schema", lambda: "org.cinnamon.desktop.interface")
    monkeypatch.setattr(system_fonts, "_setting",
                        lambda schema, key: "Liberation Sans 14" if key == "font-name" else 1.5)
    system_fonts.apply_system_font(app)
    assert app.setFont.call_args.args[0].pointSizeF() == points
    system_fonts.apply_system_font(app)
    assert app.setFont.call_count == 1
