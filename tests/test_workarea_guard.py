"""Verify changed work-area caps and an accessible compositor-owned fallback."""
import importlib
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QRect
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget


def guard_module():
    project = Path(__file__).resolve().parents[1]
    candidates = list((project / "src").glob("*/window_size.py"))
    assert len(candidates) == 1
    return importlib.import_module(candidates[0].parent.name + ".window_size")


def wait(app, predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Work-area geometry did not settle")


def plain_window():
    window = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)
    status = QLabel("Status")
    layout.addWidget(status)
    window.setCentralWidget(central)
    window._reflow = lambda: None
    return window, status


def dispose(app, window, guard):
    guard.timer.stop()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_changed_workarea_refits_and_explicit_area_bypasses_mapping(monkeypatch):
    app = QApplication.instance() or QApplication([])
    module = guard_module()
    area, lookups = [QRect(40, 30, 760, 650)], []

    def mapped(window, fallback):
        lookups.append((window, fallback))
        return area[0]

    monkeypatch.setattr(module, "available_geometry", mapped)
    window, _ = plain_window()
    window.resize(700, 780)
    font = window.font()
    guard = module.WindowSizeGuard(window, (500, 760))
    window.show()
    try:
        wait(app, lambda: window.frameGeometry().height() <= 650)
        assert lookups and window.minimumHeight() < 760
        area[0] = QRect(40, 30, 760, 500)
        guard._workarea.changed.emit()
        wait(app, lambda: window.frameGeometry().height() <= 500)
        assert window.font() == font
        lookups.clear()
        guard.update(QRect(0, 0, 800, 800))
        assert lookups == [] and window.minimumHeight() == 760
        refreshes = []
        monkeypatch.setattr(guard._workarea, "refresh", lambda: refreshes.append(True))
        guard._screen_changed()
        assert refreshes == [True] and guard.timer.isActive()
    finally:
        dispose(app, window, guard)


def test_fallback_maximize_releases_normal_floor_and_restores_it(monkeypatch):
    app = QApplication.instance() or QApplication([])
    module = guard_module()
    monkeypatch.setattr(module, "available_geometry", lambda *_: QRect(0, 0, 1280, 900))
    window, status = plain_window()
    guard = module.WindowSizeGuard(window, (700, 760))
    provider = guard._workarea
    monkeypatch.setattr(provider, "enabled", True)
    monkeypatch.setattr(provider, "refresh", lambda: None)
    monkeypatch.setattr(provider, "last_details", {"source": "captured-qt", "reason": "x11-display-unavailable"})
    window.show()
    wait(app, lambda: window.minimumHeight() == 760)
    font = window.font()
    try:
        window.showMaximized()
        wait(app, lambda: window.isMaximized() and window.minimumHeight() < 500)
        # The declared constraints permit a smaller compositor client. Exact
        # native geometry is checked separately on the supported desktops.
        assert window.minimumWidth() <= 620 and window.minimumHeight() <= 500
        assert window.font() == font and guard.preferredMinimum.height() >= 760
        assert window.rect().contains(status.geometry())
        window.showNormal()
        wait(app, lambda: not window.isMaximized() and window.minimumHeight() == 760)
        assert window.font() == font
    finally:
        dispose(app, window, guard)


def test_only_validated_wayland_constraint_requests_max_and_respects_other_states(monkeypatch):
    app = QApplication.instance() or QApplication([])
    module = guard_module()
    area = [QRect(0, 0, 800, 600)]
    monkeypatch.setattr(module, "available_geometry", lambda *_: area[0])
    window, _ = plain_window()
    guard = module.WindowSizeGuard(window, (700, 760))
    provider = guard._workarea
    monkeypatch.setattr(provider, "enabled", True)
    monkeypatch.setattr(provider, "refresh", lambda: None)
    monkeypatch.setattr(provider, "last_details", {"source": "x11-ewmh"})
    font = window.font()
    try:
        guard.update()
        assert not window.isVisible() and not window.isMaximized()
        window.show()
        wait(app, lambda: guard.constrained_native_maximum and window.isMaximized())
        assert guard.geometry_constraint["reason"] == "preferred-minimum-exceeds-validated-workarea"
        assert guard.geometry_constraint["client_capacity"][1] <= 600
        assert window.font() == font
        window.showMinimized()
        guard.update()
        app.processEvents()
        assert window.isMinimized()
        window.showFullScreen()
        guard.update()
        app.processEvents()
        assert window.isFullScreen()
        area[0] = QRect(0, 0, 1280, 1000)
        provider.changed.emit()
        wait(app, lambda: not guard.constrained_native_maximum)
        assert window.isFullScreen()
        window.showNormal()
        wait(app, lambda: not window.isMaximized() and not window.isFullScreen()
             and window.minimumHeight() == 760)
        assert window.font() == font
    finally:
        dispose(app, window, guard)


def test_geometry_diagnostics_are_debounced_private_and_style_neutral(monkeypatch, tmp_path):
    import json

    from shiboken6 import isValid

    app = QApplication.instance() or QApplication([])
    module = guard_module()
    fonts = importlib.import_module(module.__package__ + ".system_fonts")
    appearance = importlib.import_module(module.__package__ + ".appearance")
    monkeypatch.setattr(module, "available_geometry", lambda *_: QRect(0, 0, 1280, 1000))
    monkeypatch.setattr(appearance, "appearance_probes_pending", lambda app: False)
    monkeypatch.setattr(fonts.QStandardPaths, "writableLocation", lambda *_: str(tmp_path))
    original_write, writes = Path.write_text, []

    def write(path, text, *args, **kwargs):
        if path.name == "appearance.json":
            writes.append(text)
        return original_write(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write)
    original = app.property("studioAppearanceDiagnostics")
    app.setProperty("studioAppearanceDiagnostics", {"source": "fixture"})
    window, status = plain_window()
    window.setWindowTitle("/home/private-person/document-name.txt")
    status.setText("document-name.txt")
    guard = module.WindowSizeGuard(window, (500, 760))
    timer = guard._geometry_diagnostics_timer
    font, stylesheet = app.font(), app.styleSheet()
    window.show()
    try:
        wait(app, lambda: "window_geometry" in app.property("studioAppearanceDiagnostics"))
        writes.clear()
        for width in range(720, 740):
            window.resize(width, 800)
        assert timer.isActive() and timer.interval() == 100
        wait(app, lambda: app.property("studioAppearanceDiagnostics")["window_geometry"]["size"]
             == list(window.size().toTuple()) and not timer.isActive())
        assert len(writes) == 1
        window.showMinimized()
        wait(app, lambda: app.property("studioAppearanceDiagnostics")["window_geometry"]["minimized"])
        guard.geometry_constraint = dict(source={"path": "/home/private-person"},
                                         reason="/home/private-person", capacity_basis=["document-name.txt"],
                                         preferred_minimum=["/home/private-person", 1],
                                         client_capacity=[True, 2], available_geometry=[0, 0, 1, "private-person"])
        guard._publish_geometry()
        details = json.loads((tmp_path / "appearance.json").read_text())
        assert "/home" not in json.dumps(details) and "private-person" not in json.dumps(details)
        assert "document-name" not in json.dumps(details)
        values = details["window_geometry"]
        assert values["geometry_constraint"]["reason"] == "workarea-unverified"
        assert values["geometry_constraint"]["preferred_minimum"] == []
        assert values["geometry_constraint"]["client_capacity"] == []
        assert values["pending"] == dict(provider=False, layout=False, timer=guard.timer.isActive())
        assert app.font() == font and app.styleSheet() == stylesheet
        timer.start(100)
        if hasattr(guard, "stop"):
            guard.stop()
            assert not timer.isActive()
    finally:
        dispose(app, window, guard)
        app.setProperty("studioAppearanceDiagnostics", original)
    assert not isValid(timer)
