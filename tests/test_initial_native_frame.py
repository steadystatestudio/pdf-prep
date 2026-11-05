"""Keep the native decoration negotiation separate from the useful full floor."""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRect, QSize, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def case(app, monkeypatch):
    package = next((Path(__file__).parents[1] / "src").glob("*/window_size.py")).parent.name
    module = importlib.import_module(package + ".window_size")

    class Provider(QObject):
        changed = Signal()
        enabled = True
        last_details = {"source": "x11-ewmh"}

        def refresh(self):
            pass

    provider = Provider()
    area = [QRect(58, 41, 1222, 759)]
    exposure = [False]
    handle = SimpleNamespace(isExposed=lambda: exposure[0])
    handles = [handle]
    margins = [QSize(0, 0)]
    monkeypatch.setattr(module, "workarea_provider", lambda app: provider)
    monkeypatch.setattr(module, "available_geometry", lambda *_: area[0])
    monkeypatch.setattr(module, "observe_window_screen", lambda *args, **kwargs: handles[0])
    window = QMainWindow()
    local_font = QFont(window.font())
    local_font.setPointSizeF(12)
    window.setFont(local_font)
    window.setStyleSheet("QWidget { font-size: 12pt; }")
    central = QWidget()
    layout = QVBoxLayout(central)
    workspace = QWidget()
    workspace.setMinimumSize(700, 120)
    layout.addWidget(workspace, 1)
    status = QLabel("Persistent status")
    status.setMinimumHeight(40)
    layout.addWidget(status)
    window.setCentralWidget(central)
    window._reflow = lambda: None
    window.resize(1000, 738)
    monkeypatch.setattr(window, "frameGeometry", lambda: QRect(
        0, 0, window.width() + margins[0].width(), window.height() + margins[0].height()))
    max_requests = []
    monkeypatch.setattr(window, "showMaximized", lambda: max_requests.append(True))
    font = window.font()
    guard = module.WindowSizeGuard(window, (800, 736))
    try:
        yield SimpleNamespace(window=window, guard=guard, provider=provider, area=area, layout=layout,
                              workspace=workspace, status=status, exposure=exposure, margins=margins,
                              handles=handles, font=font, max_requests=max_requests)
    finally:
        if hasattr(guard, "stop"):
            guard.stop()
        else:
            guard.timer.stop()
            guard._geometry_diagnostics_timer.stop()
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


@pytest.mark.parametrize("native_handle", [False, True])
@pytest.mark.parametrize("verified", [False, True])
def test_unknown_frame_releases_only_hard_floor_without_shrinking_preference(case, native_handle, verified):
    if not native_handle:
        case.handles[0] = None
    if not verified:
        case.provider.last_details = {"source": "captured-qt", "reason": "fixture-unavailable"}
    requested = case.window.size()
    case.guard.update()
    assert case.guard.preferredMinimum == QSize(800, 736)
    assert case.guard.preferredMinimum.height() < case.area[0].height()
    assert case.window.minimumSize() == case.layout.minimumSize()
    assert case.window.minimumHeight() < 736
    assert case.window.minimumWidth() >= case.workspace.minimumWidth()
    assert case.window.size() == requested
    assert not case.window.isVisible() and not case.max_requests
    assert not case.guard.constrained_native_maximum
    assert case.window.font() == case.font and case.workspace.minimumHeight() == 120


@pytest.mark.parametrize("verified", [False, True])
def test_exposed_normal_frame_restores_full_floor_when_room_allows(case, monkeypatch, verified):
    if not verified:
        case.provider.last_details = {"source": "captured-qt", "reason": "fixture-unavailable"}
    case.guard.update()
    assert case.window.minimumHeight() < 736
    case.exposure[0] = True
    case.margins[0] = QSize(22, 60)
    monkeypatch.setattr(case.window, "isVisible", lambda: True)
    case.guard.update()
    assert case.guard._normal_frame_extra == QSize(22, 60)
    assert case.guard.geometry_constraint["client_capacity"] == [1200, 699]
    assert case.guard.constrained_native_maximum is verified
    assert bool(case.max_requests) is verified
    case.area[0] = QRect(0, 0, 1600, 1400)
    case.guard.update()
    assert case.window.minimumSize() == case.guard.preferredMinimum == QSize(800, 736)
    assert not case.guard.constrained_native_maximum
    assert case.window.font() == case.font and case.workspace.minimumHeight() == 120


@pytest.mark.parametrize("explicit_area", [False, True])
def test_native_qt_or_explicit_area_keeps_existing_full_minimum(case, explicit_area):
    if not explicit_area:
        case.provider.enabled = False
    case.guard.update(case.area[0] if explicit_area else None)
    assert case.window.minimumSize() == case.guard.preferredMinimum == QSize(800, 736)
    assert not case.max_requests and not case.window.isVisible()
    assert case.window.font() == case.font


def test_tiny_restored_window_grows_to_useful_floor_after_native_frame_is_known(case, monkeypatch):
    case.window.resize(1, 1)
    tiny = case.window.saveGeometry()
    assert case.window.restoreGeometry(tiny)
    case.guard.update()
    assert case.window.minimumSize() == case.layout.minimumSize()
    assert case.window.minimumHeight() < 736
    assert case.window.width() >= case.workspace.minimumWidth()
    assert case.window.height() >= case.workspace.minimumHeight() + case.status.minimumHeight()
    assert case.guard.preferredMinimum == QSize(800, 736)
    assert not case.max_requests and not case.window.isVisible()
    case.area[0] = QRect(0, 0, 1600, 1400)
    case.exposure[0] = True
    case.margins[0] = QSize(22, 60)
    monkeypatch.setattr(case.window, "isVisible", lambda: True)
    case.guard.update()
    assert case.window.minimumSize() == case.guard.preferredMinimum == QSize(800, 736)
    assert case.window.size().width() >= 800 and case.window.size().height() >= 736
    assert case.window.font() == case.font and not case.max_requests


def test_missing_typed_hook_uses_visible_measured_widget_frame(case, monkeypatch):
    case.handles[0] = None
    case.area[0] = QRect(0, 0, 1600, 1400)
    case.margins[0] = QSize(22, 60)
    monkeypatch.setattr(case.window, "isVisible", lambda: True)
    monkeypatch.setattr(case.window, "isActiveWindow", lambda: False)
    case.guard.update()
    assert case.guard._normal_frame_extra == QSize(22, 60)
    assert case.window.minimumSize() == case.guard.preferredMinimum == QSize(800, 736)
    assert case.window.font() == case.font and not case.max_requests


def test_missing_typed_hook_zero_frame_stays_unknown_until_typed_exposure(case, monkeypatch):
    case.handles[0] = None
    monkeypatch.setattr(case.window, "isVisible", lambda: True)
    monkeypatch.setattr(case.window, "isActiveWindow", lambda: False)
    case.guard.update()
    assert case.window.minimumSize() == case.layout.minimumSize()
    assert case.guard._normal_frame_extra is None
    assert not case.max_requests
    monkeypatch.setattr(case.window, "isActiveWindow", lambda: True)
    case.guard.update()
    assert case.guard._normal_frame_extra is None
    assert case.window.minimumSize() == case.layout.minimumSize()
    assert case.guard.geometry_constraint["capacity_basis"] == "unknown"
    case.handles[0] = SimpleNamespace(isExposed=lambda: True)
    case.guard.update()
    assert case.guard._normal_frame_extra == QSize(0, 0)
    assert case.window.minimumSize() == case.guard.preferredMinimum == QSize(800, 736)
    assert case.window.font() == case.font and not case.max_requests
