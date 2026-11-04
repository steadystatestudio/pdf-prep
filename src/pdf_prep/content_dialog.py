"""Screen-fitted read-only content viewers with persistent footer actions."""
from PySide6.QtCore import QEvent, QPoint, QRect, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFrame, QTextBrowser, QVBoxLayout

from .system_fonts import ui_html
from .desktop_geometry import available_geometry, workarea_provider


class TextContentDialog(QDialog):
    """Fit ordinary text; scroll long content without moving the Close action."""

    def __init__(self, title, text, parent=None, *, html=False, settings=None, width_key=None):
        super().__init__(parent)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self.fit_to_screen)
        workarea_provider(QApplication.instance()).changed.connect(self._fit_timer.start)
        self.setWindowTitle(title)
        self.settings, self.width_key = settings, width_key
        self.browser = QTextBrowser(self)
        self.browser.setFrameShape(QFrame.Shape.NoFrame)
        self.browser.setStyleSheet("QTextBrowser { border: 0; background: transparent; }")
        self.browser.setMinimumSize(1, 1)
        self.browser.setOpenExternalLinks(False)
        self.browser.document().setDefaultFont(QApplication.instance().font())
        if html:
            self.browser.setHtml(ui_html(text))
        else:
            self.browser.setPlainText(text)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.browser, 1)
        layout.addWidget(self.buttons)
        self._initial_width = None
        if settings is not None and width_key:
            try:
                self._initial_width = int(settings.value(width_key, 0)) or None
            except (TypeError, ValueError):
                pass

    def fit_to_screen(self):
        screen = self.screen()
        area = available_geometry(self, screen.availableGeometry()) if screen else QRect(0, 0, 1024, 768)
        frame = self.frameGeometry()
        cap_width = max(1, area.width()-max(32, frame.width()-self.width()))
        cap_height = max(1, area.height()-max(64, frame.height()-self.height()))
        margins = self.layout().contentsMargins()
        outer_w = margins.left()+margins.right()
        outer_h = margins.top()+margins.bottom()+self.layout().spacing()
        preferred = self._initial_width or self.fontMetrics().horizontalAdvance("M" * 70) + outer_w + 16
        width = min(cap_width, max(preferred, self.buttons.minimumSizeHint().width()+outer_w))
        self.browser.document().setTextWidth(max(1, width-outer_w-16))
        height = min(cap_height, int(self.browser.document().size().height())+20
                     + outer_h+self.buttons.sizeHint().height())
        self.resize(width, min(cap_height, max(self.buttons.sizeHint().height()+outer_h+40, height)))
        frame = self.frameGeometry()
        self.move(QPoint(max(area.left(), min(frame.left(), area.right()-frame.width()+1)),
                         max(area.top(), min(frame.top(), area.bottom()-frame.height()+1))))

    def showEvent(self, event):
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None and handle is not getattr(self, "_workarea_handle", None):
            handle.screenChanged.connect(self._screen_changed)
            self._workarea_handle = handle
        self._screen_changed()
        self.fit_to_screen()

    def _screen_changed(self, *_):
        screen = self.screen()
        previous = getattr(self, "_workarea_screen", None)
        if previous is not screen:
            if previous is not None:
                try:
                    previous.availableGeometryChanged.disconnect(self._screen_changed)
                except RuntimeError:
                    pass
            if screen is not None:
                screen.availableGeometryChanged.connect(self._screen_changed)
            self._workarea_screen = screen
        workarea_provider(QApplication.instance()).refresh()
        self._fit_timer.start(0)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in {QEvent.Type.FontChange, QEvent.Type.ApplicationFontChange,
                            QEvent.Type.StyleChange, QEvent.Type.DevicePixelRatioChange}:
            if hasattr(self, "browser"):
                self.browser.document().setDefaultFont(self.font())
                if hasattr(self, "_fit_timer"):
                    workarea_provider(QApplication.instance()).refresh()
                    self._fit_timer.start(0)

    def done(self, result):
        if self.settings is not None and self.width_key:
            self.settings.setValue(self.width_key, self.width())
        super().done(result)


def fit_viewer(dialog, preferred_width=950, preferred_height=760):
    """Bound image inspection windows; their actual image content may scroll."""
    screen = dialog.screen()
    area = available_geometry(dialog, screen.availableGeometry()) if screen else QRect(0, 0, 1024, 768)
    dialog.resize(min(preferred_width, max(1, area.width()-32)),
                  min(preferred_height, max(1, area.height()-64)))
