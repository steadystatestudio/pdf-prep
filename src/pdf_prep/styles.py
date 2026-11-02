"""Fusion controls with readable, palette-aware file chooser arrows."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QFileDialog, QProxyStyle, QStyle, QToolButton


def _arrow_icon(direction, palette):
    points = {
        "left": ((10, 3), (5, 8), (10, 13)),
        "right": ((6, 3), (11, 8), (6, 13)),
        "up": ((3, 10), (8, 5), (13, 10)),
    }[direction]
    icon = QIcon()
    normal = palette.color(QPalette.ColorRole.ButtonText)
    disabled = QColor(normal)
    disabled.setAlphaF(0.45)
    colors = ((QIcon.Mode.Normal, normal), (QIcon.Mode.Active, normal),
              (QIcon.Mode.Disabled, disabled),
              (QIcon.Mode.Selected, palette.color(QPalette.ColorRole.HighlightedText)))
    for mode, color in colors:
        for size in (16, 32, 48):
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(size / 16, size / 16)
            painter.setPen(QPen(color, 2, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            path = QPainterPath()
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
            painter.drawPath(path)
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon


class FileDialogStyle(QProxyStyle):
    def __init__(self):
        super().__init__("Fusion")

    def standardIcon(self, standard_icon, option=None, widget=None):
        # Fusion's bundled back/forward/up glyphs can remain black after an app
        # switches to a dark palette. Limit the replacement to file dialogs.
        owner = widget
        while owner is not None and not isinstance(owner, QFileDialog):
            owner = owner.parentWidget()
        if owner is not None:
            rtl = owner.layoutDirection() == Qt.LayoutDirection.RightToLeft
            direction = {
                QStyle.StandardPixmap.SP_ArrowBack: "right" if rtl else "left",
                QStyle.StandardPixmap.SP_ArrowForward: "left" if rtl else "right",
                QStyle.StandardPixmap.SP_ArrowUp: "up",
            }.get(standard_icon)
            if direction is not None:
                return _arrow_icon(direction, owner.palette())
        return super().standardIcon(standard_icon, option, widget)


def refresh_file_dialog_icons(app):
    # Dialogs cache standard QIcons. Refresh only their navigation arrows when
    # System appearance changes while a file chooser is already open.
    kinds = (("backButton", QStyle.StandardPixmap.SP_ArrowBack),
             ("forwardButton", QStyle.StandardPixmap.SP_ArrowForward),
             ("toParentButton", QStyle.StandardPixmap.SP_ArrowUp))
    for dialog in app.topLevelWidgets():
        if isinstance(dialog, QFileDialog):
            for name, kind in kinds:
                button = dialog.findChild(QToolButton, name)
                if button is not None:
                    button.setIcon(app.style().standardIcon(kind, None, dialog))
