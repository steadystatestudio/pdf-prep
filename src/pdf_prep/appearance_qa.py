"""Explicit package diagnostics; invoked only by the existing smoke checks."""

import json
from pathlib import Path
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QSpinBox,
    QTextEdit,
    QWidget,
)

from .system_fonts import polish_system_text


def capture_appearance(app, window, destination):
    """Measure native text inheritance and the real compact layout in the bundle."""
    destination = Path(destination) / "appearance"
    destination.mkdir(parents=True, exist_ok=False)
    font, size = QFont(app.font()), window.size()
    surfaces = (
        QAbstractButton,
        QAbstractItemView,
        QComboBox,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMenu,
        QSpinBox,
        QTextEdit,
    )
    records = []

    def settle():
        deadline = time.monotonic() + 0.15
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)

    try:
        for label, points in (("native", font.pointSizeF()), ("large-layout", max(18, font.pointSizeF()))):
            enlarged = QFont(font)
            if points > 0:
                enlarged.setPointSizeF(points)
            app.setFont(enlarged)
            settle()
            polish_system_text(app)
            widgets = []
            for widget in window.findChildren(QWidget):
                if not isinstance(widget, surfaces) or widget.property("studioContentTypography"):
                    continue
                # Qt stylesheets resolve point sizes into pixels on some platforms.
                actual, expected = widget.fontInfo().pixelSize(), window.fontInfo().pixelSize()
                assert actual == expected, (type(widget).__name__, widget.objectName(), actual, expected)
                widgets.append(dict(type=type(widget).__name__, name=widget.objectName(), pixels=actual))
            heights = []
            for width in (1280, 960, 820, 1):
                window.resize(width, min(800, window.screen().availableGeometry().height() - 50))
                settle()
                area, frame = window.screen().availableGeometry(), window.frameGeometry()
                assert frame.width() <= area.width() + 2 and frame.height() <= area.height() + 2, (
                    label,
                    frame.getRect(),
                    area.getRect(),
                    window.minimumSize().toTuple(),
                )
                assert window.width() >= window.minimumWidth()
                if hasattr(window, "action_row") and hasattr(window, "rename"):
                    row = window.action_row
                    controls, primary, undo = row.controls, window.rename, window.undo
                    if row.width() >= row.minimumSizeHint().width():
                        assert primary.x() >= controls.geometry().right() + 8
                        assert primary.geometry().right() + 8 <= undo.x()
                        assert primary.y() == undo.y()
                        heights.append(row.height())
                records.append(
                    dict(
                        font_case=label,
                        requested_width=width,
                        size=window.size().toTuple(),
                        minimum=window.minimumSize().toTuple(),
                        font=window.font().toString(),
                        text_surfaces=len(widgets),
                    )
                )
            assert len(set(heights)) <= 1, heights
            assert window.grab().save(str(destination / (label + "-minimum.png")))
            window.resize(size)
            settle()
            assert window.grab().save(str(destination / (label + ".png")))
            help_action = next(
                (
                    getattr(window, name)
                    for name in ("show_help", "_show_help", "_help")
                    if callable(getattr(window, name, None))
                ),
                None,
            )
            if help_action:
                help_errors = []

                def inspect_help():
                    dialog = app.activeModalWidget()
                    try:
                        assert dialog is not None, "Help did not open"
                        polish_system_text(app)
                        for widget in dialog.findChildren(QWidget):
                            if isinstance(widget, surfaces):
                                assert widget.fontInfo().pixelSize() == window.fontInfo().pixelSize(), type(
                                    widget
                                ).__name__
                        for editor in dialog.findChildren(QTextEdit):
                            block = editor.document().begin()
                            while block.isValid():
                                fragment = block.begin()
                                while not fragment.atEnd():
                                    item = fragment.fragment()
                                    if item.isValid():
                                        explicit = item.charFormat().fontPointSize()
                                        assert explicit == 0 or abs(explicit - app.font().pointSizeF()) < 0.01
                                    fragment += 1
                                block = block.next()
                        assert dialog.grab().save(str(destination / (label + "-help.png")))
                    except Exception as exc:
                        help_errors.append(repr(exc))
                    finally:
                        if dialog:
                            dialog.close()

                QTimer.singleShot(150, inspect_help)
                help_action()
                assert not help_errors, help_errors
    finally:
        app.setFont(font)
        window.resize(size)
        settle()
    (destination / "report.json").write_text(
        json.dumps(
            dict(passed=True, native=app.property("studioAppearanceDiagnostics"), cases=records, text_surfaces=widgets),
            indent=2,
        ),
        encoding="utf-8",
    )
