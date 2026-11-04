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


def test_explicit_qt_settings_integration_is_respected(monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    from pdf_prep import appearance

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "X-Cinnamon")
    monkeypatch.setenv("QT_QPA_PLATFORMTHEME", "qt6ct")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    directory = tmp_path / "qt6ct"
    directory.mkdir()
    settings = QSettings(str(directory / "qt6ct.conf"), QSettings.IniFormat)
    settings.setValue("Fonts/general", QFont("Sans Serif", 17).toString())
    settings.sync()
    assert appearance.qt6ct_provider()["font"].pointSizeF() == 17


def test_missing_desktop_configuration_is_safe(monkeypatch, tmp_path):
    from pdf_prep import appearance

    monkeypatch.setenv("QT_QPA_PLATFORMTHEME", "qt6ct")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "missing"))
    assert appearance.qt6ct_provider() == {"reason": "configuration-unavailable"}


@pytest.mark.parametrize("dpr,points", [(1.5, 14), (1.0, 21)])
def test_provider_scaling_is_not_applied_twice(monkeypatch, dpr, points):
    from unittest.mock import Mock
    from pdf_prep import appearance

    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    monkeypatch.delenv("QT_SCREEN_SCALE_FACTORS", raising=False)
    normalized = appearance.gtk_font(QFont(), {
        "font": {"family": "Liberation Sans", "point_size": 14, "absolute_size": False},
        "font_dpi": 144, "widget_scale": 1,
    }, 96, dpr)
    assert normalized.pointSizeF() == pytest.approx(points)
    app = Mock(spec=["font", "setFont", "_studio_pilot_configured", "_studio_appearance_resolved_font"])
    app._studio_pilot_configured = True
    app._studio_appearance_resolved_font = normalized
    app.font.return_value = QFont("Liberation Sans", 14)
    app.setFont.side_effect = lambda value: setattr(app.font, "return_value", value)
    system_fonts.apply_system_font(app)
    assert app.font().pointSizeF() == pytest.approx(points)
    applications = app.setFont.call_count
    system_fonts.apply_system_font(app)
    assert app.setFont.call_count == applications <= 1


def test_stylesheet_baseline_includes_native_tables_and_new_dialogs(tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QDialog, QTableWidget
    from pdf_prep.appearance import configure_appearance

    app = QApplication.instance() or QApplication([])
    previous_font, previous_style = QFont(app.font()), app.styleSheet()
    previous_settings = getattr(app, "_studio_pilot_settings", None)
    try:
        chosen = QFont("Sans Serif", 18)
        settings = QSettings(str(tmp_path / "appearance.ini"), QSettings.IniFormat)
        settings.setValue("appearance/font_mode", "custom")
        settings.setValue("appearance/font_family", chosen.family())
        settings.setValue("appearance/font_size_pt", chosen.pointSizeF())
        configure_appearance(app, settings, theme_key="appearance")
        app.setStyleSheet("QWidget {" + system_fonts.system_font_css(app) + "color: #222;}"
                         "QHeaderView::section { padding: 9px; }")
        dialog = QDialog()
        table = QTableWidget(2, 2, dialog)
        dialog.ensurePolished()
        table.ensurePolished()
        app.processEvents()
        for widget in (dialog, table, table.horizontalHeader(), table.verticalHeader()):
            assert widget.font().pointSizeF() == 18
            assert widget.font().family() == chosen.family()
        dialog.close()
    finally:
        configure_appearance(app, previous_settings, theme_key="appearance")
        app.setStyleSheet(previous_style)
        app.setFont(previous_font)
