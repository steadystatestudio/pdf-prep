"""Regression checks for useful minimum windows and monitor changes."""
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QRect, QSettings
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pdf_prep.app import MainWindow


@pytest.fixture
def sized_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    previous_font = app.font()
    previous_style = app.styleSheet()
    app.setFont(QFont("Segoe UI", 9))
    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope, str(tmp_path))
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.IniFormat)
    window = MainWindow(settings)
    area = [QRect(0, 0, 3440, 1392)]
    screen = SimpleNamespace(availableGeometry=lambda: area[0],
                             availableGeometryChanged=app.primaryScreen().availableGeometryChanged)
    monkeypatch.setattr(window, "screen", lambda: screen)
    window.show()
    QTest.qWait(50)
    yield window, area
    window.close()
    app.setFont(previous_font)
    app.setStyleSheet(previous_style)


def test_dragging_smaller_keeps_complete_workspace(sized_window):
    window, _ = sized_window
    window.resize(1, 1)
    QTest.qWait(80)
    assert window.width() >= 980
    assert window.height() >= 720
    scroll = getattr(window, "work_scroll", None)
    if scroll is not None:
        assert scroll.horizontalScrollBar().maximum() == 0
        assert scroll.verticalScrollBar().maximum() == 0
    assert window.size() == window.minimumSize()


def test_smaller_monitor_caps_minimum_then_restores_it(sized_window):
    window, area = sized_window
    normal = window.minimumSize()
    original_font = window.font()
    area[0] = QRect(0, 0, 800, 600)
    window.window_size_guard.update()
    QTest.qWait(80)
    assert window.frameGeometry().width() <= 800
    assert window.frameGeometry().height() <= 600
    assert window.font() == original_font
    area[0] = QRect(0, 0, 3440, 1392)
    window.window_size_guard.update()
    QTest.qWait(80)
    assert window.minimumWidth() >= normal.width()
    assert window.minimumHeight() >= normal.height()


def test_larger_font_grows_minimum_without_shrinking_text(sized_window):
    window, _ = sized_window
    before = window.minimumSize()
    font = window.font()
    font.setPointSize(18)
    window.setFont(font)
    QTest.qWait(80)
    assert window.font().pointSize() == 18
    assert window.minimumWidth() > before.width()
    assert window.minimumHeight() > before.height()
