"""Keep a useful desktop workspace visible at the smallest allowed window size."""
from PySide6.QtCore import QEvent, QObject, QPoint, QSize, QTimer


class WindowSizeGuard(QObject):
    """Protect the complete working layout; allow overflow only on small screens.

    Baselines describe each app's useful workspace in logical pixels at a normal
    UI font. Actual layout requirements can increase them. Qt already accounts
    for display scaling, so devicePixelRatio must not be applied again.
    """

    def __init__(self, window, baseline, scroll=None):
        super().__init__(window)
        self.window = window
        self.baseline = QSize(*baseline)
        self.scroll = scroll
        self._screen = None
        self._handle = None
        self._busy = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.update)
        window.installEventFilter(self)
        self.timer.start(0)

    def eventFilter(self, watched, event):
        if not self._busy and event.type() in {
            QEvent.Show, QEvent.Resize, QEvent.FontChange,
            QEvent.ApplicationFontChange, QEvent.StyleChange,
        }:
            self.timer.start(0)
        return False

    def update(self, available=None):
        if self._busy:
            return
        self._busy = True
        try:
            window = self.window
            handle = window.windowHandle()
            if handle is not None and handle is not self._handle:
                handle.screenChanged.connect(lambda *_: self.timer.start(0))
                self._handle = handle
            screen = window.screen()
            if screen is not self._screen:
                if self._screen is not None:
                    self._screen.availableGeometryChanged.disconnect(self._screen_changed)
                screen.availableGeometryChanged.connect(self._screen_changed)
                self._screen = screen
            area = available if available is not None else screen.availableGeometry()
            frame = window.frameGeometry()
            extra_w = max(0, frame.width() - window.width())
            extra_h = max(0, frame.height() - window.height())
            cap = QSize(max(1, area.width() - extra_w), max(1, area.height() - extra_h))
            scale = max(1.0, window.fontMetrics().height() / 16.0)
            wanted = QSize(round(self.baseline.width() * scale), round(self.baseline.height() * scale))
            central = window.centralWidget()
            layout = central.layout()
            layout.activate()
            wanted = wanted.expandedTo(layout.minimumSize())
            if self.scroll is not None:
                work = self.scroll.widget()
                margins = layout.contentsMargins()
                outer_width = margins.left() + margins.right()
                wanted.setWidth(max(wanted.width(), work.minimumSizeHint().width() + outer_width))
                width = min(wanted.width(), cap.width())
                content_width = max(1, width - outer_width - 2 * self.scroll.frameWidth())
                work_layout = work.layout()
                work_height = max(work.minimumSizeHint().height(), work_layout.totalHeightForWidth(content_width))
                # Sum the outer vertical layout's real minimums. Its preferred
                # height can include a large scroll sizeHint and must not be added
                # to the complete content height a second time.
                heights = []
                for index in range(layout.count()):
                    item = layout.itemAt(index)
                    if item.isEmpty():
                        continue
                    if item.widget() is self.scroll:
                        heights.append(work_height + 2 * self.scroll.frameWidth())
                    else:
                        heights.append(max(item.minimumSize().height(),
                                           item.heightForWidth(content_width) if item.hasHeightForWidth() else 0))
                complete_height = (sum(heights) + max(0, len(heights) - 1) * layout.spacing()
                                   + margins.top() + margins.bottom())
                wanted.setHeight(max(wanted.height(), complete_height))
            minimum = wanted.boundedTo(cap)
            self.preferredMinimum = wanted
            if window.minimumSize() != minimum:
                window.setMinimumSize(minimum)
            if not (window.isMaximized() or window.isFullScreen() or window.isMinimized()):
                size = window.size().expandedTo(minimum).boundedTo(cap)
                if window.size() != size:
                    window.resize(size)
                frame = window.frameGeometry()
                position = QPoint(max(area.left(), min(frame.left(), area.right() - frame.width() + 1)),
                                  max(area.top(), min(frame.top(), area.bottom() - frame.height() + 1)))
                if position != frame.topLeft():
                    window.move(position)
        finally:
            self._busy = False

    def _screen_changed(self, *_):
        self.timer.start(0)
