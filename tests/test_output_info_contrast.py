"""The prepared-output label must contrast with its actual inherited surface."""
from collections import Counter

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from pdf_prep import appearance, system_fonts
from pdf_prep.app import MainWindow


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_app(app, tmp_path, monkeypatch):
    font, style, palette = app.font(), app.styleSheet(), QPalette(app.palette())
    previous_settings = getattr(app, "_studio_pilot_settings", None)
    previous_key = getattr(app, "_studio_appearance_theme_key", "appearance")
    monkeypatch.setattr(app, "_studio_refresh_appearance", None, raising=False)
    monkeypatch.setattr(system_fonts.QStandardPaths, "writableLocation", lambda *_: str(tmp_path / "diagnostics"))
    for provider, function in (("gtk", "_start_native"), ("portal", "_start_portal")):
        monkeypatch.setattr(appearance, function,
                            lambda instance, generation, name=provider: appearance._provider_finished(
                                instance, generation, name, {"reason": "fixture-unavailable"}))
    try:
        yield app
    finally:
        appearance.configure_appearance(app, previous_settings, refresh=True, theme_key=previous_key)
        app.setStyleSheet(style)
        app.setPalette(palette)
        app.setFont(font)
        app.processEvents()


def pixel_counts(image):
    return Counter(image.pixelColor(x, y).name() for y in range(image.height()) for x in range(image.width()))


@pytest.mark.parametrize("theme,background", [("light", "#f3f3f3"), ("dark", "#2c2c2c")])
@pytest.mark.parametrize("accent", ["#d34f91", "#2768ad", "#a94dc1"], ids=["pink", "blue", "purple"])
def test_prepared_output_text_uses_real_window_background(isolated_app, tmp_path, theme, background, accent):
    app = isolated_app
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    settings.setValue("appearance", theme)
    settings.setValue("appearance/accent_mode", "custom")
    settings.setValue("appearance/accent", accent)
    settings.setValue("appearance/font_mode", "custom")
    settings.setValue("appearance/font_family", app.font().family())
    settings.setValue("appearance/font_size_pt", 12)
    window = MainWindow(settings)
    try:
        window.output_info.setText("Prepared: 3 pages · 0.02 MB (19,000 bytes)")
        window.show()
        app.processEvents()
        label = window.output_info
        label.ensurePolished()
        # Hold only the label's size while comparing painted versus empty
        # contents. This prevents blank text from changing its layout height.
        label.setFixedSize(label.size())
        foreground = label.palette().color(label.foregroundRole()).name()
        painted = label.grab().toImage()
        label.clear()
        app.processEvents()
        empty = label.grab().toImage()
        empty_pixels = pixel_counts(empty)
        assert empty_pixels == {background: empty.width() * empty.height()}
        assert pixel_counts(painted)[foreground] > 0, "Verify the resolved foreground is actually painted"
        assert system_fonts.contrast_ratio(foreground, background) >= 4.5, (foreground, background, theme, accent)
        assert app.font().pointSizeF() == label.font().pointSizeF() == 12
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
