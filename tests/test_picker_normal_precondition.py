"""A late restored configure must be fitted before strict normal picker QA."""
import os

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRect, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QVBoxLayout,
)

from pdf_prep import appearance_qa as qa
from pdf_prep import qa_file_dialog as picker


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("reject_final", [False, True])
@pytest.mark.parametrize("initial_maximum", [False, True])
def test_restore_refits_late_normal_size_without_bypassing_final_check(
    app, monkeypatch, reject_final, initial_maximum,
):
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    edit = QLineEdit("unchanged synthetic input")
    buttons = QDialogButtonBox(QDialogButtonBox.Open | QDialogButtonBox.Cancel)
    layout.addWidget(edit)
    layout.addWidget(buttons)
    font = QFont(dialog.font())
    font.setPointSizeF(18)
    dialog.setFont(font)
    dialog.setStyleSheet("QWidget { font-size: 18pt; }")
    area = dialog.screen().availableGeometry()
    dialog.resize(min(600, area.width() - 40), min(400, area.height() - 80))
    monkeypatch.setattr(picker, "available_geometry", lambda window, raw: area)
    monkeypatch.setattr(qa, "available_geometry", lambda window, raw: area)
    monkeypatch.setattr(qa, "appearance_probes_pending", lambda app: False)
    monkeypatch.setattr(qa, "workarea_probes_pending", lambda app: False)
    monkeypatch.setattr(dialog, "frameGeometry", lambda: QRect(0, 0, dialog.width() + 14, dialog.height() + 40))
    request = qa._request_qa_state
    configured, checked = [], []
    configure_scheduled = False
    timers = []

    def state_request(window, maximized):
        nonlocal configure_scheduled
        request(window, maximized)
        if not maximized and not configure_scheduled:
            configure_scheduled = True
            timer = QTimer(dialog)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: (dialog.resize(dialog.width(), area.height() + 20), configured.append(True)))
            timer.start(40)
            timers.append(timer)

    monkeypatch.setattr(qa, "_request_qa_state", state_request)

    def strict_final(window):
        qa._settle_geometry(app, window)
        assert not window.isMaximized()
        assert not qa._qa_window_handle(window).windowState()
        assert window.frameGeometry().height() <= area.height()
        assert window.frameGeometry().width() <= area.width()
        assert configured and edit.text() == "unchanged synthetic input"
        assert all(button.font().pointSizeF() == 18 for button in buttons.buttons())
        checked.append(True)
        if reject_final:
            raise AssertionError("final-normal-check-rejected")

    try:
        if initial_maximum:
            dialog.showMaximized()
        else:
            dialog.showNormal()
            timer = QTimer(dialog)
            timer.setSingleShot(True)
            timer.timeout.connect(dialog.showMaximized)
            timer.start(40)
            timers.append(timer)
        app.processEvents()
        if reject_final:
            with pytest.raises(AssertionError, match="final-normal-check-rejected"):
                picker._prepare_normal_picker(app, dialog, strict_final)
        else:
            picker._prepare_normal_picker(app, dialog, strict_final)
        assert checked == [True] and configured == [True]
        assert dialog.font().pointSizeF() == 18
    finally:
        for timer in timers:
            timer.stop()
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def test_picker_never_acknowledging_normal_fails_within_bound(app, monkeypatch):
    dialog = QDialog()
    clock = [0.0]
    monkeypatch.setattr(picker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(picker.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(qa, "_request_qa_state", lambda window, maximized: None)
    monkeypatch.setattr(qa, "_qa_window_handle", lambda window: None)
    monkeypatch.setattr(dialog, "isMaximized", lambda: True)
    monkeypatch.setattr(qa, "appearance_probes_pending", lambda app: False)
    monkeypatch.setattr(qa, "workarea_probes_pending", lambda app: False)
    final = []

    def strict_final(window):
        qa._settle_geometry(app, window, timeout=.1)
        final.append(True)

    try:
        with pytest.raises(AssertionError, match="geometry-did-not-settle"):
            picker._prepare_normal_picker(app, dialog, strict_final, timeout=.1)
        assert not final and .1 <= clock[0] < .2
        assert all(not timer.isActive() for timer in dialog.findChildren(QTimer))
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


def test_already_normal_picker_never_requires_maximization(app, monkeypatch):
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    edit = QLineEdit("unchanged synthetic input")
    buttons = QDialogButtonBox(QDialogButtonBox.Open | QDialogButtonBox.Cancel)
    layout.addWidget(edit)
    layout.addWidget(buttons)
    font = QFont(dialog.font())
    font.setPointSizeF(18)
    dialog.setFont(font)
    dialog.setStyleSheet("QWidget { font-size: 18pt; }")
    dialog.resize(600, 250)
    area = dialog.screen().availableGeometry()
    request = qa._request_qa_state
    requests = []
    monkeypatch.setattr(picker, "available_geometry", lambda window, raw: area)
    monkeypatch.setattr(qa, "available_geometry", lambda window, raw: area)
    monkeypatch.setattr(qa, "appearance_probes_pending", lambda app: False)
    monkeypatch.setattr(qa, "workarea_probes_pending", lambda app: False)

    def normal_only(window, maximized):
        assert not maximized, "A normal picker must not depend on maximizing support"
        requests.append(maximized)
        request(window, maximized)

    monkeypatch.setattr(qa, "_request_qa_state", normal_only)
    try:
        dialog.showNormal()
        app.processEvents()
        assert not dialog.isMaximized() and not qa._qa_window_handle(dialog).windowState()
        picker._prepare_normal_picker(app, dialog, lambda window: qa._settle_geometry(app, window))
        assert requests == [False]
        assert edit.text() == "unchanged synthetic input"
        assert all(button.font().pointSizeF() == 18 for button in buttons.buttons())
        assert qa._qa_footprint_fits(dialog, dialog.frameGeometry(), area)
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
