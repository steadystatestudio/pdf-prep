"""Pre-map fitting may run while hidden, but never after its guard stops."""
import importlib
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRect, Signal
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget


def test_hidden_prefit_does_not_revive_a_stopped_guard(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    package = next((Path(__file__).parents[1] / "src").glob("*/window_size.py")).parent.name
    module = importlib.import_module(package + ".window_size")
    fonts = importlib.import_module(package + ".system_fonts")
    monkeypatch.setattr(fonts.QStandardPaths, "writableLocation", lambda *args: str(tmp_path))

    class WorkArea(QObject):
        changed = Signal()
        enabled = True
        last_details = {"source": "x11-ewmh"}

        def refresh(self):
            pass

    provider = WorkArea()
    monkeypatch.setattr(module, "workarea_provider", lambda app: provider)
    monkeypatch.setattr(module, "available_geometry", lambda *args: QRect(0, 0, 1280, 752))
    window = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)
    layout.addWidget(QLabel("Persistent content"))
    window.setCentralWidget(central)
    window._reflow = lambda: None
    guard = module.WindowSizeGuard(window, (800, 900))
    try:
        guard.update()
        assert not window.isVisible() and guard.constrained_native_maximum
        assert guard.preferredMinimum.height() == 900 and window.minimumHeight() < 752
        guard.stop()
        minimum = window.minimumSize()
        monkeypatch.setattr(module, "available_geometry", lambda *args: (_ for _ in ()).throw(
            AssertionError("A stopped guard must not consult desktop geometry")))
        guard.update()
        provider.changed.emit()
        app.processEvents()
        assert window.minimumSize() == minimum and not window.isVisible()
        assert not guard.timer.isActive() and not guard._geometry_diagnostics_timer.isActive()
    finally:
        guard.stop()
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
