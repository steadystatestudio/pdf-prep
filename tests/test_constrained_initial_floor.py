"""Small verified work areas keep a useful workspace without a full-content hard floor."""
import importlib
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRect, QSettings, Signal
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

PACKAGE = next((Path(__file__).parents[1] / "src").glob("*/window_size.py")).parent.name
GUARD = importlib.import_module(PACKAGE + ".window_size")


@pytest.fixture
def guarded_case(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    appearance = importlib.import_module(PACKAGE + ".appearance")
    fonts = importlib.import_module(PACKAGE + ".system_fonts")
    monkeypatch.setattr(fonts.QStandardPaths, "writableLocation", lambda *args: str(tmp_path))
    app.processEvents()
    font = app.font()
    old_settings = getattr(app, "_studio_pilot_settings", None)
    old_key = getattr(app, "_studio_appearance_theme_key", "theme")
    monkeypatch.setattr(app, "_studio_refresh_appearance", None, raising=False)
    for name, function in (("gtk", "_start_native"), ("portal", "_start_portal")):
        monkeypatch.setattr(appearance, function,
            lambda instance, generation, name=name: appearance._provider_finished(
                instance, generation, name, {"reason": "fixture-unavailable"}))
    settings = QSettings(str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat)
    settings.setValue("appearance/font_mode", "custom")
    settings.setValue("appearance/font_family", font.family())
    points = font.pointSizeF()
    settings.setValue("appearance/font_size_pt", points if 6 <= points <= 72 else 9)
    appearance.configure_appearance(app, settings, refresh=True)
    app.processEvents()

    class FixedWorkArea(QObject):
        changed = Signal()
        enabled = True
        last_details = {"source": "x11-ewmh"}

        def refresh(self):
            pass

    provider = FixedWorkArea()
    area = [QRect(58, 48, 1222, 752)]
    monkeypatch.setattr(GUARD, "workarea_provider", lambda app: provider)
    monkeypatch.setattr(GUARD, "available_geometry", lambda *args: area[0])
    window = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)
    workspace = QWidget()
    workspace.setMinimumSize(700, 120)
    layout.addWidget(workspace, 1)
    status = QLabel("Persistent status")
    status.setMinimumHeight(40)
    layout.addWidget(status)
    window.setCentralWidget(central)
    window.resize(900, 1000)
    window._reflow = lambda: None
    guard = GUARD.WindowSizeGuard(window, (800, 900))

    def wait(predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError("The constrained work-area policy did not settle")

    try:
        yield SimpleNamespace(app=app, window=window, layout=layout, workspace=workspace,
                              status=status, provider=provider, area=area, guard=guard, wait=wait)
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
        appearance.configure_appearance(app, old_settings, refresh=True, theme_key=old_key)
        app.processEvents()
        app.setFont(font)
        app.processEvents()


@pytest.mark.parametrize("verified", [True, False])
def test_hidden_minimum_preserves_workspace_and_restores_complete_floor(guarded_case, verified):
    case = guarded_case
    window, guard = case.window, case.guard
    if not verified:
        case.provider.last_details = {"source": "captured-qt", "reason": "fixture-unavailable"}
    font = window.font()
    guard.update()
    assert not window.isVisible() and not window.isMaximized()
    assert guard.constrained_native_maximum is verified
    assert guard.preferredMinimum.height() >= 900
    # Until a native Wayland frame is exposed, both verified and fallback
    # providers retain the exact persistent floor for compositor negotiation.
    # Only the verified constraint is allowed to request maximization below.
    assert window.minimumSize() == case.layout.minimumSize().boundedTo(case.area[0].size())
    assert window.minimumHeight() < case.area[0].height()
    assert window.minimumWidth() >= case.workspace.minimumWidth()
    assert window.font() == font and case.workspace.minimumHeight() == 120
    window.show()
    if verified:
        case.wait(lambda: window.isMaximized())
    case.wait(lambda: window.rect().contains(case.status.geometry()))
    assert case.workspace.height() >= 120
    case.area[0] = QRect(0, 0, 1600, 1400)
    case.provider.changed.emit()
    case.wait(lambda: not guard.constrained_native_maximum)
    window.showNormal()
    case.wait(lambda: not window.isMaximized() and window.minimumSize() == guard.preferredMinimum)
    assert window.font() == font and case.workspace.minimumHeight() == 120


def test_explicit_geometry_override_keeps_complete_floor(guarded_case):
    case = guarded_case
    case.guard.update(case.area[0])
    assert not case.guard.constrained_native_maximum
    assert case.window.minimumSize() == case.guard.preferredMinimum.boundedTo(case.area[0].size())
    assert not case.window.isVisible()
