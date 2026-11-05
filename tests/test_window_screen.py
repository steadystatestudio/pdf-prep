"""Typed native hooks must remain optional when Qt reuses a wrapper address."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRect, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QWidgetItem

PACKAGE = next(path.parent.name for path in (Path(__file__).parents[1] / "src").glob("*/window_screen.py"))
HOOKS = importlib.import_module(PACKAGE + ".window_screen")
FONTS = importlib.import_module(PACKAGE + ".system_fonts")
GEOMETRY = importlib.import_module(PACKAGE + ".desktop_geometry")
CONTROLS = importlib.import_module(PACKAGE + ".appearance_controls")


@pytest.fixture
def app(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    instance = QApplication.instance() or QApplication([])
    properties = {key: instance.property(key) for key in (
        "studioAppearanceDiagnostics", "studioNativeWindowHookDiagnostics")}
    monkeypatch.setattr(FONTS, "_save_diagnostics", lambda app, value: app.setProperty(
        "studioAppearanceDiagnostics", value))
    yield instance
    for key, value in properties.items():
        instance.setProperty(key, value)


def dispose(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_valid_qwindow_connects_and_stop_disconnects(app):
    window = QWidget()
    calls = []
    try:
        window.show()
        handle = HOOKS.observe_window_screen(window, lambda: calls.append(True))
        observer = window._studio_screen_observer
        assert HOOKS._valid_window(handle)
        handle.screenChanged.emit(window.screen())
        QTest.qWait(20)
        assert calls and observer.details["state"] == "connected"
        observer.stop()
        count = len(calls)
        handle.screenChanged.emit(window.screen())
        window.move(window.x() + 10, window.y() + 10)
        QTest.qWait(20)
        assert len(calls) == count
        assert observer.request() is None
    finally:
        dispose(window)


def test_wrong_native_wrapper_is_bounded_and_widget_events_still_refit(app, monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / "appearance.ini"), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    area = QRect(0, 0, 1200, 900)
    monkeypatch.setattr(GEOMETRY, "available_geometry", lambda window, fallback: QRect(area))
    dialog = CONTROLS.AppearanceSettingsDialog(settings)
    wrong = QWidgetItem(QWidget())
    attempts = []

    def wrong_handle():
        attempts.append(True)
        return wrong

    monkeypatch.setattr(dialog, "windowHandle", wrong_handle)
    try:
        assert not HOOKS._valid_window(wrong)  # isValid alone accepts this real native item.
        from shiboken6 import isValid
        assert isValid(wrong)
        font = dialog.fontInfo().pixelSize()
        dialog.show()
        QTest.qWait(250)
        observer = dialog._studio_screen_observer
        assert len(attempts) == observer.MAX_ATTEMPTS
        assert observer.details["state"] == "fallback"
        assert observer.details["reason"] == "native-window-wrapper-unavailable"
        assert not observer.retry_timer.isActive()
        count = len(attempts)
        QTest.qWait(120)
        assert len(attempts) == count
        # An actual widget Move event must refit against a newly reported work
        # area even though a native screenChanged signal cannot be connected.
        area.setSize(QRect(0, 0, 500, 360).size())
        dialog.move(dialog.x() + 12, dialog.y() + 12)
        QTest.qWait(80)
        assert dialog.width() <= 468 and dialog.height() <= 296
        assert dialog.minimumSize() == dialog.maximumSize()
        assert dialog.fontInfo().pixelSize() == font
    finally:
        dispose(dialog)


def test_late_native_handle_is_connected_within_bounded_retry(app, monkeypatch):
    window = QWidget()
    calls = []
    attempts = []

    def delayed_handle():
        attempts.append(True)
        return None if len(attempts) == 1 else QWidget.windowHandle(window)

    monkeypatch.setattr(window, "windowHandle", delayed_handle)
    try:
        window.show()
        assert HOOKS.observe_window_screen(window, lambda: calls.append(True)) is None
        QTest.qWait(90)
        observer = window._studio_screen_observer
        assert len(attempts) == 2 and HOOKS._valid_window(observer._handle)
        assert not observer.retry_timer.isActive()
        observer._handle.screenChanged.emit(window.screen())
        QTest.qWait(20)
        assert calls
    finally:
        dispose(window)


def test_parent_owned_filter_is_inert_after_python_state_is_cleared(app):
    window = QWidget()
    observer = HOOKS.WindowScreenObserver(window, lambda: None, window)
    try:
        observer.stop()
        # Python's cyclic-wrapper teardown can clear the attribute before Qt
        # delivers the remaining native destruction events.
        del observer._stopped
        assert observer.eventFilter(window, QEvent(QEvent.Type.Destroy)) is False
    finally:
        observer._stopped = True
        dispose(window)


def test_observer_ignores_a_deleted_window_before_its_owner_is_deleted(app):
    from shiboken6 import isValid
    owner = QWidget()
    window = QWidget()
    observer = HOOKS.WindowScreenObserver(window, lambda: None, owner)
    try:
        dispose(window)
        assert not isValid(window) and isValid(observer)
        assert observer.eventFilter(window, QEvent(QEvent.Type.Move)) is False
        assert observer.request() is None
        observer._changed()
        assert not observer.change_timer.isActive()
    finally:
        dispose(owner)


def _assert_reuse_probe_result(result):
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert isinstance(record, dict)
    if record.get("reproduced") is False:
        assert set(record) == {"reproduced", "iterations", "reason"}
        assert type(record["iterations"]) is int and record["iterations"] == 10000
        assert record["reason"] == "bounded-natural-address-reuse-unavailable"
        assert not result.stderr.strip()
        pytest.skip("Bounded natural address reuse was unavailable; deterministic wrapper tests remain mandatory")
    assert record.get("reproduced") is True
    assert type(record["iteration"]) is int and 0 <= record["iteration"] < 10000
    assert record["kind"] == "QWidgetItem"
    for key in ("same_wrapper", "same_native_pointer", "old_connection_failed", "fallback_recalculated"):
        assert record[key] is True


@pytest.mark.skipif(sys.platform != "win32", reason="Real native-address reuse reproducer uses the Windows allocator")
def test_real_qt_address_reuse_old_connection_fails_new_observer_preserves_events(tmp_path):
    # Fresh process: preserve the test runner's wrapper map and application
    # preferences. No Qt methods/slots are mocked or replaced in this probe.
    script = r'''
import importlib,json,sys,weakref
import shiboken6
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtGui import QWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QGridLayout,QPushButton,QWidget
hooks=importlib.import_module(sys.argv[1])
app=QApplication(["Studio native wrapper regression"])
app.setApplicationName("Studio Wrapper Regression")
bad=None
for index in range(10000):
 parent=QWidget();layout=QGridLayout(parent);child=QPushButton("Owned probe",parent)
 layout.addWidget(child,0,0)
 item=layout.itemAt(0);pointer=shiboken6.getCppPointer(item)[0];reference=weakref.ref(item)
 del item
 layout.addWidget(child,1,0)
 dialog=QDialog();dialog.winId();handle=dialog.windowHandle()
 if not isinstance(handle,QWindow):
  assert handle is reference() and shiboken6.getCppPointer(handle)[0]==pointer
  assert shiboken6.isValid(handle)
  bad=dict(reproduced=True,iteration=index,kind=type(handle).__name__,same_wrapper=True,same_native_pointer=True)
  try:handle.screenChanged.connect(lambda *_:None)
  except AttributeError:bad["old_connection_failed"]=True
  else:raise AssertionError("Old native hook did not expose the reproduced type mismatch")
  calls=[]
  assert hooks.observe_window_screen(dialog,lambda:calls.append(True)) is None
  dialog.show();QTest.qWait(250)
  observer=dialog._studio_screen_observer
  assert observer.details["state"]=="fallback" and not observer.retry_timer.isActive()
  before=len(calls);dialog.move(dialog.x()+12,dialog.y()+12);QTest.qWait(30)
  assert len(calls)>before
  assert hooks.observe_window_screen(dialog,lambda:None) is None
  bad["fallback_recalculated"]=True
  dialog.close();parent.close()
  break
 dialog.deleteLater();parent.deleteLater()
 QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
if bad is None:
 bad=dict(reproduced=False,iterations=10000,reason="bounded-natural-address-reuse-unavailable")
print(json.dumps(bad))
'''
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                       PYTHONPATH=str(Path(__file__).parents[1] / "src"))
    result = subprocess.run([sys.executable, "-c", script, PACKAGE + ".window_screen"],
                            env=environment, capture_output=True, text=True, timeout=30, check=False)
    _assert_reuse_probe_result(result)
