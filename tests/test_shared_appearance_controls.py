"""Staged production settings and per-application preference compatibility."""
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication, QPushButton

from pdf_prep import appearance
from pdf_prep.appearance_controls import AppearanceControls, AppearanceSettingsDialog


@pytest.fixture
def controls(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    font = QFont(app.font())
    monkeypatch.setattr(appearance, "_start_native", lambda *_: None)
    monkeypatch.setattr(appearance, "_start_portal", lambda *_: None)
    settings = QSettings(str(tmp_path / "preferences.ini"), QSettings.IniFormat)
    settings.setFallbacksEnabled(False)
    settings.setValue("ui/appearance", "dark")
    settings.setValue("unrelated", "keep")
    widget = AppearanceControls(settings, theme_key="ui/appearance")
    yield app, settings, widget
    widget.close()
    app.setFont(font)


def choose(control, value):
    index = control.findData(value)
    assert index >= 0
    control.setCurrentIndex(index)


def test_save_preserves_native_theme_key_and_applies_whole_font(controls):
    app, settings, widget = controls
    choose(widget.theme, "light")
    choose(widget.font_mode, "custom")
    widget.font_family.setCurrentFont(QFont("DejaVu Sans"))
    widget.font_size.setValue(18)
    choose(widget.accent_mode, "custom")
    widget.accent_color = QColor("#d34f91")
    assert settings.value("ui/appearance") == "dark"
    widget.save_settings()
    assert settings.value("ui/appearance") == "light"
    assert not settings.contains("theme")
    appearance.configure_appearance(app, settings, theme_key="ui/appearance")
    assert app.font().pointSizeF() == 18
    assert app._studio_appearance_values["color_scheme"] == "light"
    assert app._studio_appearance_values["accent"] == "#d34f91"
    assert settings.value("unrelated") == "keep"


def test_reset_is_staged_and_leaves_unrelated_preferences(controls):
    _, settings, widget = controls
    settings.setValue("appearance/font_mode", "custom")
    settings.setValue("appearance/font_size_pt", 18)
    widget.reset_appearance_button.click()
    assert settings.value("appearance/font_mode") == "custom"
    assert settings.value("ui/appearance") == "dark"
    widget.save_settings()
    assert settings.value("appearance/font_mode") == "system"
    assert settings.value("ui/appearance") == "system"
    assert not settings.contains("appearance/font_size_pt")
    assert settings.value("unrelated") == "keep"


def test_refresh_keeps_unsaved_choices_and_no_public_copy_button(controls):
    app, settings, widget = controls
    choose(widget.font_mode, "custom")
    widget.font_size.setValue(23)
    choose(widget.theme, "light")
    widget.refresh_appearance_button.click()
    app.processEvents()
    assert widget.font_size.value() == 23
    assert widget.theme.currentData() == "light"
    assert settings.value("ui/appearance") == "dark"
    assert not any("copy appearance" in button.text().lower() for button in widget.findChildren(QPushButton))


def test_cancel_does_not_save_and_form_fits_without_unnecessary_scroll(controls):
    app, settings, _ = controls
    app.setFont(QFont(app.font().family(), 10))
    dialog = AppearanceSettingsDialog(settings, theme_key="ui/appearance")
    dialog.show()
    app.processEvents()
    choose(dialog.controls.theme, "light")
    assert dialog.minimumSize() == dialog.maximumSize()
    assert dialog.scroll.verticalScrollBar().maximum() == 0
    dialog.reject()
    assert settings.value("ui/appearance") == "dark"


def test_custom_size_boundaries_are_supported(controls):
    _, settings, widget = controls
    choose(widget.font_mode, "custom")
    for points in (6, 72):
        widget.font_size.setValue(points)
        widget.save_settings()
        assert settings.value("appearance/font_size_pt", type=float) == points


def test_pending_fit_is_cancelled_when_dialog_is_deleted(controls):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    app, settings, _ = controls
    dialog = AppearanceSettingsDialog(settings, theme_key="ui/appearance")
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(dialog)
    app.processEvents()
