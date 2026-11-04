"""Keep strict normal geometry checks distinct from validated small-screen mode."""
from types import SimpleNamespace

import pytest
from pdf_prep import appearance
from pdf_prep.smoke import _settle_minimum_geometry
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QWidget


def prepare(monkeypatch, window):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(appearance, "appearance_probes_pending", lambda app: False)
    window.busy = False
    window.window_size_guard = SimpleNamespace(timer=QTimer(window), preferredMinimum=window.minimumSize(),
                                              constrained_native_maximum=False, geometry_constraint={})
    window.show()
    app.processEvents()
    return app


def test_normal_minimum_waits_for_late_layout_change(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.setMinimumSize(220, 170)
    prepare(monkeypatch, window)
    QTimer.singleShot(35, lambda: window.setMinimumSize(260, 190))
    try:
        result = _settle_minimum_geometry(app, window, timeout=1, stable_interval=.05)
        assert result["geometry_mode"] == "exact-normal-minimum"
        assert result["size"] == result["minimum"] == [260, 190]
    finally:
        window.close()


class CompositorWindow(QWidget):
    # Model the guard re-maximizing a normal request under a genuine constraint.
    def isMaximized(self):
        return True

    def showNormal(self):
        pass


def constrained_window(monkeypatch):
    window = CompositorWindow()
    window.setMinimumSize(220, 170)
    window.resize(440, 320)
    app = prepare(monkeypatch, window)
    guard = window.window_size_guard
    guard.constrained_native_maximum = True
    guard.geometry_constraint = {"source": "x11-ewmh",
                                 "reason": "preferred-minimum-exceeds-validated-workarea",
                                 "preferred_minimum": [500, 700], "client_capacity": [440, 320]}
    window.status_label = QLabel("Status", window)
    window.status_label.setGeometry(10, 250, 200, 30)
    window.status_label.show()
    return app, window


def test_verified_constrained_mode_checks_persistent_control_bounds(monkeypatch):
    app, window = constrained_window(monkeypatch)
    try:
        result = _settle_minimum_geometry(app, window, timeout=1, stable_interval=.03)
        assert result["geometry_mode"] == "validated-constrained-maximized"
        assert result["size"] != result["minimum"]
        window.status_label.move(10, window.height() - 5)
        with pytest.raises(AssertionError, match="status_label"):
            _settle_minimum_geometry(app, window, timeout=1, stable_interval=.03)
    finally:
        window.close()


def test_unverified_maximized_window_does_not_bypass_exact_minimum(monkeypatch):
    app, window = constrained_window(monkeypatch)
    window.window_size_guard.geometry_constraint["source"] = "captured-qt"
    try:
        with pytest.raises(AssertionError, match="Exact minimum geometry did not settle"):
            _settle_minimum_geometry(app, window, timeout=.15, stable_interval=.03)
    finally:
        window.close()


def test_unmeasured_constraint_does_not_bypass_exact_minimum(monkeypatch):
    app, window = constrained_window(monkeypatch)
    window.window_size_guard.geometry_constraint['preferred_minimum'] = [440, 320]
    try:
        with pytest.raises(AssertionError, match="Exact minimum geometry did not settle"):
            _settle_minimum_geometry(app, window, timeout=.15, stable_interval=.03)
    finally:
        window.close()
