"""QA must test a real normal QWindow and attainable client resize requests."""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QApplication, QWidget, QWidgetItem

PACKAGE = next((Path(__file__).parents[1] / "src").glob("*/appearance_qa.py")).parent.name
QA = importlib.import_module(PACKAGE + ".appearance_qa")


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    instance.processEvents()


class WidgetState:
    def __init__(self, handle):
        self.handle = handle
        self.maximized = False
        self.requests = []

    def windowHandle(self):
        return self.handle

    def showMaximized(self):
        self.maximized = True
        self.requests.append("max")

    def showNormal(self):
        self.maximized = False
        self.requests.append("normal")


def test_widget_normal_does_not_hide_a_maximized_qwindow(app):
    handle = QWindow()
    handle.showMaximized()
    app.processEvents()
    window = WidgetState(handle)
    assert not window.maximized and handle.windowState() == Qt.WindowState.WindowMaximized
    QA._request_qa_state(window, False)
    app.processEvents()
    assert not window.maximized and handle.windowState() == Qt.WindowState.WindowNoState
    handle.close()
    handle.deleteLater()
    app.processEvents()


def test_layout_item_wrapper_is_never_used_as_a_qwindow(app):
    child = QWidget()
    item = QWidgetItem(child)
    window = WidgetState(item)
    assert QA._qa_window_handle(window) is None
    QA._request_qa_state(window, False)
    assert window.requests == ["normal"]
    child.deleteLater()
    app.processEvents()


def test_genuinely_constrained_native_maximum_is_not_forced_normal(monkeypatch):
    window = WidgetState(None)
    window.window_size_guard = SimpleNamespace(constrained_native_maximum=True,
        geometry_constraint={"source": "x11-ewmh"})
    monkeypatch.setattr(
        QA, "_settle_geometry", lambda *args, **kwargs: pytest.fail("Constrained maximum must stay native")
    )
    QA._prepare_qa_normal_state(None, window)
    assert window.requests == []


def test_normal_resize_requests_use_measured_frame_extents():
    window = SimpleNamespace(frameGeometry=lambda: QRect(0, 0, 824, 640), width=lambda: 800, height=lambda: 600)
    area = QRect(58, 32, 1222, 768)
    assert QA._qa_resize_request(window, 1280, area) == (1198, 728)
    assert QA._qa_resize_request(window, 1, area) == (1, 728)
