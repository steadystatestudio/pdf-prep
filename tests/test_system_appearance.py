"""Behavioral coverage for native appearance and app-prose sizing."""

import os
import pytest
from PySide6.QtGui import QColor, QFont, QPalette, QTextDocument
from PySide6.QtWidgets import QApplication
from pdf_prep import system_fonts as appearance


def test_desktop_helper_uses_host_libraries_without_changing_parent(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/appimage/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/lib")
    monkeypatch.setenv("PYTHONHOME", "/tmp/appimage")
    monkeypatch.setenv("GI_TYPELIB_PATH", "/tmp/appimage/typelib")
    clean = appearance.host_environment()
    assert clean["LD_LIBRARY_PATH"] == "/host/lib"
    assert "PYTHONHOME" not in clean and "GI_TYPELIB_PATH" not in clean
    assert os.environ["LD_LIBRARY_PATH"] == "/tmp/appimage/_internal"
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    assert "LD_LIBRARY_PATH" not in appearance.host_environment()


@pytest.mark.parametrize("accent", ["#e83e8c", "#a94dc1", "#0078d4", "#ffff00", "#ffffff", "#000000", "#777777"])
def test_accent_states_keep_readable_text(monkeypatch, accent):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app, "_studio_host_appearance", {"accent": accent, "source": "test"}, raising=False)
    monkeypatch.setattr(app, "_studio_native_palette", QPalette(), raising=False)
    colors = appearance.accent_colors(app)
    assert colors["accent"] == accent
    for role in ("accent", "hover", "pressed"):
        assert appearance.contrast_ratio(colors[role], colors["on_accent"]) >= 4.5


def test_failed_host_query_preserves_captured_native_accent(monkeypatch):
    app = QApplication.instance() or QApplication([])
    native = QPalette()
    native.setColor(QPalette.Accent, QColor("#d9469a"))
    monkeypatch.setattr(app, "_studio_native_palette", native, raising=False)
    monkeypatch.setattr(app, "_studio_host_appearance", {"fallback_reason": "OSError"}, raising=False)
    before = QPalette(app.palette())
    try:
        replacement = QPalette()
        replacement.setColor(QPalette.Accent, QColor("#123456"))
        app.setPalette(replacement)
        assert appearance.accent_colors(app)["accent"] == "#d9469a"
        assert app.property("studioAppearanceDiagnostics")["fallback_reason"] == "OSError"
    finally:
        app.setPalette(before)


def test_app_html_keeps_heading_and_small_text_at_system_size():
    _app = QApplication.instance() or QApplication([])
    document = QTextDocument()
    document.setDefaultFont(QFont("Sans Serif", 18))
    document.setHtml(appearance.ui_html("<h2>Help</h2><p>Body <small>note</small></p>"))
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                size = fragment.charFormat().fontPointSize()
                assert size in (0, 18)
            iterator += 1
        block = block.next()


@pytest.mark.parametrize(
    "failure", [FileNotFoundError(), OSError("host library unavailable"), ValueError("invalid preferences")]
)
def test_windows_host_query_failure_retains_a_diagnostic(monkeypatch, failure):
    monkeypatch.setattr(appearance.sys, "platform", "win32")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(appearance, "_run_host", fail)
    result = appearance._host_appearance()
    assert result["source"] == "native Qt fallback"
    assert result["fallback_reason"] == type(failure).__name__


def test_plain_filename_and_document_content_typography_are_preserved():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QTextEdit

    app = QApplication.instance() or QApplication([])
    label = QLabel("<h2>literal filename</h2>")
    label.setTextFormat(Qt.PlainText)
    content = QTextEdit()
    content.setProperty("studioContentTypography", True)
    content.setHtml("<h1>Document content</h1>")
    before = content.toHtml()
    appearance.polish_system_text(app)
    assert label.text() == "<h2>literal filename</h2>"
    assert content.toHtml() == before
    label.close()
    content.close()


def test_invalid_accent_records_native_fallback(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app, "_studio_native_palette", QPalette(), raising=False)
    monkeypatch.setattr(app, "_studio_host_appearance", {"accent": "invalid"}, raising=False)
    appearance.accent_colors(app)
    record = app.property("studioAppearanceDiagnostics")
    assert record["accent_source"] == "captured native Qt palette"
    assert record["fallback_reason"]
