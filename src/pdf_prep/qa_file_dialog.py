"""Inspect Qt-owned file pickers only inside explicit package QA."""

import time
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QFontInfo
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QLineEdit,
    QToolTip,
    QWidget,
)

from .desktop_geometry import available_geometry


def _normal_picker_capacity(dialog, area):
    """Keep the known native frame, even while QWidget's frame cache lags."""
    from .appearance_qa import _qa_window_handle

    frame = dialog.frameGeometry()
    extra_width = max(0, frame.width() - dialog.width())
    extra_height = max(0, frame.height() - dialog.height())
    handle = _qa_window_handle(dialog)
    if handle is not None:
        native_frame = handle.frameGeometry()
        extra_width = max(extra_width, native_frame.width() - handle.width())
        extra_height = max(extra_height, native_frame.height() - handle.height())
    return max(1, area.width() - extra_width), max(1, area.height() - extra_height)


def _prepare_normal_picker(app, dialog, settle, *, timeout=5):
    """Fit observed normal frames and acknowledge at most one unexpected Max."""
    from .appearance_qa import _qa_window_handle, _request_qa_state

    deadline = time.monotonic() + timeout
    maximum_acknowledged = False
    callback_errors = []
    timer = QTimer(dialog)
    timer.setInterval(20)

    def reconcile_state():
        nonlocal maximum_acknowledged
        if time.monotonic() >= deadline:
            timer.stop()
            return
        handle = _qa_window_handle(dialog)
        native_state = handle.windowState() if handle is not None else None
        observed_maximum = dialog.isMaximized() or (
            native_state is not None and bool(native_state & Qt.WindowState.WindowMaximized))
        if observed_maximum:
            if not maximum_acknowledged:
                maximum_acknowledged = True
                _request_qa_state(dialog, False)
            return
        if native_state is None or not native_state:
            area = available_geometry(dialog, dialog.screen().availableGeometry())
            width, height = _normal_picker_capacity(dialog, area)
            target = min(dialog.width(), width), min(dialog.height(), height)
            if target != dialog.size().toTuple():
                dialog.resize(*target)

    def reconcile_observed_state():
        try:
            reconcile_state()
        except Exception as error:
            if not callback_errors:
                callback_errors.append(error)
            timer.stop()

    timer.timeout.connect(reconcile_observed_state)
    try:
        reconcile_observed_state()
        if callback_errors:
            raise callback_errors[0]
        timer.start()
        # The existing strict settle owns the deadline/stability/font/min/frame
        # checks; the owned timer only responds to states/frames actually seen.
        try:
            settle(dialog)
        except BaseException:
            if callback_errors:
                raise callback_errors[0]
            raise
        if callback_errors:
            raise callback_errors[0]
        if time.monotonic() > deadline:
            raise AssertionError("picker-normal-acknowledgement-deadline")
    finally:
        timer.stop()
        timer.deleteLater()


@contextmanager
def preserved_file_dialog_preferences(settings=None):
    """Restore only Qt's shared picker group after this explicit QA check."""
    if settings is None:
        settings = QSettings(QSettings.NativeFormat, QSettings.UserScope, "QtProject")
    settings.setFallbacksEnabled(False)
    settings.sync()
    assert settings.status() == QSettings.NoError, "Qt file-picker preferences could not be read"
    had_group = "FileDialog" in settings.childGroups()
    settings.beginGroup("FileDialog")
    saved = {key: settings.value(key) for key in settings.allKeys()}
    settings.endGroup()
    try:
        yield
    finally:
        settings.sync()
        settings.beginGroup("FileDialog")
        for key in set(settings.allKeys()) - set(saved):
            settings.remove(key)
        for key, value in saved.items():
            if settings.value(key) != value:
                settings.setValue(key, value)
        settings.endGroup()
        settings.sync()
        if not had_group:
            settings.remove("FileDialog")
            settings.sync()
        settings.beginGroup("FileDialog")
        restored = {key: settings.value(key) for key in settings.allKeys()}
        settings.endGroup()
        if (
            settings.status() != QSettings.NoError
            or restored != saved
            or (not had_group and "FileDialog" in settings.childGroups())
        ):
            raise AssertionError("Qt file-picker preferences did not restore")


def inspect_file_dialog(app, window, destination, surfaces, settle):
    """Inspect real Qt picker typography, cancel, and preserve its synthetic root."""
    destination = Path(destination)
    root = destination / "file-dialog-synthetic"
    assert root.resolve().is_relative_to(destination.resolve()) and not root.is_symlink()
    root.mkdir(exist_ok=True)
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    expected = window.fontInfo().pixelSize()
    family = window.fontInfo().family()
    assert QFontInfo(QToolTip.font()).pixelSize() == expected, "Tooltip font differs"
    records = []
    selected = []
    canceled = False
    with preserved_file_dialog_preferences():
        dialog = QFileDialog(window, "Package file picker", str(root))
        try:
            dialog.setOption(QFileDialog.DontUseNativeDialog, True)
            dialog.setOption(QFileDialog.ReadOnly, True)
            dialog.setOption(QFileDialog.DontUseCustomDirectoryIcons, True)
            dialog.setAcceptMode(QFileDialog.AcceptOpen)
            dialog.setFileMode(QFileDialog.ExistingFile)
            dialog.setDirectory(str(root))
            dialog.setHistory([str(root)])
            dialog.setSidebarUrls([QUrl.fromLocalFile(str(root))])
            edit = dialog.findChild(QLineEdit, "fileNameEdit")
            if edit:
                edit.clear()
            for view in dialog.findChildren(QAbstractItemView):
                view.clearSelection()
            dialog.fileSelected.connect(lambda value: selected.append(True))
            dialog.filesSelected.connect(lambda values: selected.append(True))
            dialog.show()
            app.processEvents()
            area = available_geometry(dialog, dialog.screen().availableGeometry())
            cap_width, cap_height = _normal_picker_capacity(dialog, area)
            dialog.resize(min(dialog.width(), cap_width), min(dialog.height(), cap_height))
            _prepare_normal_picker(app, dialog, settle)
            assert dialog.testOption(QFileDialog.DontUseNativeDialog)
            assert Path(dialog.directory().absolutePath()).resolve() == root.resolve()
            for widget in dialog.findChildren(QWidget):
                if isinstance(widget, surfaces):
                    assert widget.fontInfo().pixelSize() == expected, (
                        type(widget).__name__,
                        widget.objectName(),
                        "picker-font-size",
                    )
                    assert widget.fontInfo().family().casefold() == family.casefold(), (
                        type(widget).__name__,
                        widget.objectName(),
                        "picker-font-family",
                    )
                    records.append({"type": type(widget).__name__, "name": widget.objectName(), "pixels": expected})
            assert len(records) >= 8, "File picker widgets did not materialize"
            dialog.reject()
            canceled = dialog.result() == QDialog.Rejected
            assert canceled and not selected and not dialog.isVisible()
        finally:
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
    after = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    assert before == after, "Canceled picker changed a synthetic file"
    return {
        "qt_preferences_restored": True,
        "qt_owned": True,
        "canceled": canceled,
        "no_selection": not selected,
        "synthetic_files_unchanged": before == after,
        "text_surfaces": records,
        "tooltip_pixels": expected,
    }
