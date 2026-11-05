"""Picker QA retains native frames, strict failure behavior, and owned cleanup."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QFileDialog

from pdf_prep import appearance_qa as qa
from pdf_prep import qa_file_dialog as picker


@pytest.fixture(scope="session")
def app():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
    application = QApplication.instance() or QApplication([])
    registered_fonts = []
    if os.name == "nt" and not QFontDatabase.families():
        for name in ("segoeui.ttf", "seguisb.ttf"):
            font = Path(os.environ["WINDIR"]) / "Fonts" / name
            identifier = QFontDatabase.addApplicationFont(str(font))
            assert identifier >= 0
            registered_fonts.append(identifier)
    try:
        yield application
    finally:
        for identifier in registered_fonts:
            assert QFontDatabase.removeApplicationFont(identifier)


@pytest.fixture
def dialog(app, tmp_path):
    with picker.preserved_file_dialog_preferences():
        value = QFileDialog(None, "Controlled synthetic picker", str(tmp_path))
        value.setOption(QFileDialog.Option.DontUseNativeDialog)
        value.setFont(QFont("Segoe UI" if os.name == "nt" else app.font().family(), 18))
        value.setStyleSheet("QWidget {font-size:18pt;}")
        value.resize(700, 450)
        value.show()
        app.processEvents()
        yield value
        value.close()
        value.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def test_native_frame_capacity_retains_known_frame_when_widget_lags(monkeypatch):
    value = SimpleNamespace(frameGeometry=lambda: QRect(0, 0, 681, 481), width=lambda:681, height=lambda:481)
    native = SimpleNamespace(frameGeometry=lambda:QRect(0, 0, 695, 521), width=lambda:681,
                             height=lambda:481, frameMargins=lambda:None)
    monkeypatch.setattr(qa, "_qa_window_handle", lambda window:native)
    assert picker._normal_picker_capacity(value, QRect(39, 22, 814, 511)) == (800, 471)


def test_unavailable_native_frame_keeps_widget_fallback(monkeypatch):
    value = SimpleNamespace(frameGeometry=lambda:QRect(0, 0, 714, 490), width=lambda:700, height=lambda:450)
    monkeypatch.setattr(qa, "_qa_window_handle", lambda window:None)
    assert picker._normal_picker_capacity(value, QRect(0, 0, 814, 511)) == (800, 471)


def owned_one_shot(dialog, delay, function):
    timer = QTimer(dialog)
    timer.setSingleShot(True)
    timer.timeout.connect(function)
    timer.start(delay)
    return timer


def test_late_actual_maximum_is_acknowledged_once_without_forced_max(app, dialog, monkeypatch):
    requests = []
    original = qa._request_qa_state

    def request(window, maximum):
        requests.append(maximum)
        return original(window, maximum)

    monkeypatch.setattr(qa, "_request_qa_state", request)
    font = dialog.fontInfo().pixelSize()
    controlled = owned_one_shot(dialog, 60, lambda:original(dialog, True))
    picker._prepare_normal_picker(app, dialog, lambda window:qa._settle_geometry(app, window))
    assert not controlled.isActive()
    assert requests == [False, False]
    assert not dialog.isMaximized() and not qa._qa_window_handle(dialog).windowState()
    assert dialog.fontInfo().pixelSize() == font == 24
    assert all(not timer.isActive() for timer in dialog.findChildren(QTimer))


def test_second_unexpected_maximum_fails_strictly_and_stops_owned_timer(app, dialog, monkeypatch):
    original = qa._request_qa_state
    requests = []

    def request(window, maximum):
        requests.append(maximum)
        return original(window, maximum)

    monkeypatch.setattr(qa, "_request_qa_state", request)
    one = owned_one_shot(dialog, 30, lambda:original(dialog, True))
    two = owned_one_shot(dialog, 95, lambda:original(dialog, True))
    with pytest.raises(AssertionError, match="geometry-did-not-settle"):
        picker._prepare_normal_picker(app, dialog,
            lambda window:qa._settle_geometry(app, window, timeout=.35), timeout=.4)
    assert not one.isActive() and not two.isActive()
    assert requests == [False, False], "Never repeatedly drive native state until a test passes"
    assert dialog.isMaximized()
    assert all(not timer.isActive() for timer in dialog.findChildren(QTimer))
    original(dialog, False)


def test_later_measured_frame_growth_is_refitted_during_final_settle(app, dialog, monkeypatch):
    height = [500]
    monkeypatch.setattr(picker, "_normal_picker_capacity", lambda window, area:(800, height[0]))
    transition = owned_one_shot(dialog, 60, lambda:height.__setitem__(0, 350))
    font = dialog.fontInfo().pixelSize()
    picker._prepare_normal_picker(app, dialog, lambda window:qa._settle_geometry(app, window))
    assert not transition.isActive() and dialog.height() == 350
    assert dialog.fontInfo().pixelSize() == font == 24
    assert all(not timer.isActive() for timer in dialog.findChildren(QTimer))


def test_strict_settle_failure_propagates_with_timer_stopped(app, dialog):
    def fail(window):
        app.processEvents()
        raise AssertionError("controlled-strict-failure")

    with pytest.raises(AssertionError, match="controlled-strict-failure"):
        picker._prepare_normal_picker(app, dialog, fail)
    assert all(not timer.isActive() for timer in dialog.findChildren(QTimer))


def test_delayed_capacity_callback_error_cannot_be_hidden_by_valid_final_geometry(app, dialog, monkeypatch):
    original = picker._normal_picker_capacity
    fail = [False]

    def capacity(window, area):
        if fail[0]:
            raise RuntimeError("controlled-delayed-capacity-failure")
        return original(window, area)

    monkeypatch.setattr(picker, "_normal_picker_capacity", capacity)
    trigger = owned_one_shot(dialog, 60, lambda:fail.__setitem__(0, True))
    with pytest.raises(RuntimeError, match="controlled-delayed-capacity-failure"):
        picker._prepare_normal_picker(app, dialog, lambda window:qa._settle_geometry(app, window))
    assert not trigger.isActive() and not dialog.isMaximized()
    assert all(not timer.isActive() for timer in dialog.findChildren(QTimer))


def test_delayed_native_ack_error_is_retained_instead_of_only_eventual_geometry_timeout(app, dialog, monkeypatch):
    original = qa._request_qa_state
    requests = []

    def request(window, maximum):
        requests.append(maximum)
        if len(requests) == 2:
            raise RuntimeError("controlled-delayed-native-ack-failure")
        return original(window, maximum)

    monkeypatch.setattr(qa, "_request_qa_state", request)
    trigger = owned_one_shot(dialog, 60, lambda:original(dialog, True))
    with pytest.raises(RuntimeError, match="controlled-delayed-native-ack-failure"):
        picker._prepare_normal_picker(app, dialog,
            lambda window:qa._settle_geometry(app, window, timeout=.3), timeout=.4)
    assert requests == [False, False] and not trigger.isActive()
    assert all(not timer.isActive() for timer in dialog.findChildren(QTimer))
    original(dialog, False)
