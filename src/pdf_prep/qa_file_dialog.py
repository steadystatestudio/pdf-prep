"""Inspect Qt-owned file pickers only inside explicit package QA."""

from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QUrl
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
            frame = dialog.frameGeometry()
            cap_width = max(1, area.width() - max(0, frame.width() - dialog.width()))
            cap_height = max(1, area.height() - max(0, frame.height() - dialog.height()))
            dialog.resize(min(dialog.width(), cap_width), min(dialog.height(), cap_height))
            settle(dialog)
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
