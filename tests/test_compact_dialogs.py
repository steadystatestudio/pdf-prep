"""Ordinary text fits; long viewer content scrolls independently of Close."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pdf_prep.content_dialog import TextContentDialog


@pytest.mark.parametrize("long_content,area", [
    (False, QRect(0, 0, 1280, 900)),
    (True, QRect(0, 0, 640, 480)),
])
def test_fitted_text_viewer_keeps_footer_visible(monkeypatch, long_content, area):
    app = QApplication.instance() or QApplication([])
    text = "A short, complete explanation." if not long_content else "Complete details remain readable.\n" * 200
    dialog = TextContentDialog("Help", text)
    monkeypatch.setattr(dialog, "screen", lambda: SimpleNamespace(availableGeometry=lambda: area))
    dialog.show()
    QTest.qWait(50)
    try:
        assert area.contains(dialog.frameGeometry())
        assert dialog.buttons.isVisible()
        assert dialog.rect().contains(dialog.buttons.geometry())
        assert (dialog.browser.verticalScrollBar().maximum() > 0) is long_content
    finally:
        dialog.close()
        app.processEvents()
