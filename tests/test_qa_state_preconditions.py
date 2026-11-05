"""Intermediate native-state acknowledgement must not weaken final geometry."""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect, QSize, Qt

PACKAGE = next((Path(__file__).parents[1] / "src").glob("*/appearance_qa.py")).parent.name
QA = importlib.import_module(PACKAGE + ".appearance_qa")


@pytest.mark.parametrize("normal,client_height,native_maximum,pending,passes", [
    (False, 1369, True, False, True),
    (True, 1369, False, False, False),
    (False, 1400, True, False, False),
    (False, 1369, False, False, False),
    (False, 1369, True, True, False),
])
def test_windows_maximum_overscan_is_only_an_intermediate_precondition(
    monkeypatch, normal, client_height, native_maximum, pending, passes,
):
    # Actual Windows 11 values from the failed current-source MKV workflow:
    # the maximized client fits; its invisible non-client borders overscan.
    area, frame = QRect(0, 0, 3440, 1392), QRect(0, -8, 3440, 1400)
    state = dict(maximum=not normal, clock=0.0)
    window = SimpleNamespace(
        screen=lambda: SimpleNamespace(availableGeometry=lambda: area),
        frameGeometry=lambda: frame,
        size=lambda: QSize(3440, client_height),
        minimumSize=lambda: QSize(780, 538),
        width=lambda: 3440,
        height=lambda: client_height,
        minimumWidth=lambda: 780,
        minimumHeight=lambda: 538,
        fontInfo=lambda: SimpleNamespace(pixelSize=lambda: 12),
        isMaximized=lambda: state["maximum"],
        showMaximized=lambda: state.update(maximum=True),
        showNormal=lambda: state.update(maximum=False),
    )
    handle = SimpleNamespace(
        windowState=lambda: Qt.WindowState.WindowMaximized if native_maximum else Qt.WindowState.WindowNoState,
        showMaximized=lambda: None,
        showNormal=lambda: None,
    )
    monkeypatch.setattr(QA, "_qa_window_handle", lambda window: handle)
    monkeypatch.setattr(QA, "available_geometry", lambda window, raw: area)
    monkeypatch.setattr(QA, "appearance_probes_pending", lambda app: pending)
    monkeypatch.setattr(QA, "workarea_probes_pending", lambda app: False)
    monkeypatch.setattr(QA.time, "monotonic", lambda: state["clock"])
    monkeypatch.setattr(QA.time, "sleep", lambda seconds: state.update(clock=state["clock"] + seconds))
    app = SimpleNamespace(processEvents=lambda: None)
    if passes:
        QA._settle_geometry(app, window, normal=normal, timeout=.3)
        assert state["maximum"] and state["clock"] >= .12
    else:
        with pytest.raises(AssertionError, match="geometry-did-not-settle"):
            QA._settle_geometry(app, window, normal=normal, timeout=.3)
