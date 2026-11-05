"""Keep a useful desktop workspace visible at the smallest allowed window size."""
from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication

from .desktop_geometry import available_geometry, workarea_provider
from .window_screen import observe_window_screen, stop_window_screen_observer


class WindowSizeGuard(QObject):
    """Protect the complete working layout; allow overflow only on small screens.

    Baselines reserve useful preview/list space in logical pixels. Actual control
    measurements, rather than a font multiplier on that space, set the minimum. Qt already accounts
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
        self._normal_frame_extra = None
        self.constrained_native_maximum = False
        self.geometry_constraint = {}
        self._stopped = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.update)
        self._geometry_diagnostics_timer = QTimer(self)
        self._geometry_diagnostics_timer.setSingleShot(True)
        self._geometry_diagnostics_timer.timeout.connect(self._publish_geometry)
        self._workarea = workarea_provider(QGuiApplication.instance())
        self._workarea.changed.connect(self._workarea_changed)
        window.installEventFilter(self)
        self.timer.start(0)

    def eventFilter(self, watched, event):
        if not getattr(self, "_stopped", False) and event.type() in {
            QEvent.Show, QEvent.Resize, QEvent.WindowStateChange, QEvent.FontChange,
            QEvent.ApplicationFontChange, QEvent.StyleChange, QEvent.LayoutRequest,
        }:
            self._geometry_diagnostics_timer.start(100)
        if self._stopped:
            return False
        if event.type() == QEvent.Hide:
            self.timer.stop()
            return False
        if not self._busy and event.type() in {
            QEvent.Show, QEvent.Resize, QEvent.WindowActivate,
            QEvent.WindowStateChange, QEvent.DevicePixelRatioChange, QEvent.FontChange,
            QEvent.ApplicationFontChange, QEvent.StyleChange, QEvent.LayoutRequest,
        }:
            if event.type() != QEvent.LayoutRequest:
                self._workarea.refresh()
            self.timer.start(0)
        return False

    def update(self, available=None):
        if self._stopped or self._busy:
            return
        self._busy = True
        try:
            window = self.window
            handle = observe_window_screen(window, self._screen_changed, owner=self)
            self._handle = handle
            screen = window.screen()
            if screen is None:
                return
            if screen is not self._screen:
                if self._screen is not None:
                    self._screen.availableGeometryChanged.disconnect(self._screen_changed)
                screen.availableGeometryChanged.connect(self._screen_changed)
                self._screen = screen
            area = (available if available is not None
                    else available_geometry(window, screen.availableGeometry()))
            frame = window.frameGeometry()
            extra_w = max(0, frame.width() - window.width())
            extra_h = max(0, frame.height() - window.height())
            cap = QSize(max(1, area.width() - extra_w), max(1, area.height() - extra_h))
            compositor_fallback = (available is None and self._workarea.enabled
                                   and self._workarea.last_details.get("source") != "x11-ewmh"
                                   and (window.isMaximized() or window.isFullScreen()))
            wanted = QSize(self.baseline)
            if hasattr(window, "compact_minimum_width"):
                wanted.setWidth(max(wanted.width(), window.compact_minimum_width()))
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
                if complete_height > cap.height():
                    # A required vertical bar must not manufacture horizontal
                    # overflow when the monitor can fit the complete form width.
                    bar_width = self.scroll.verticalScrollBar().sizeHint().width()
                    required = (work.minimumSizeHint().width() + outer_width
                                + 2 * self.scroll.frameWidth() + bar_width)
                    wanted.setWidth(max(wanted.width(), required))
            if self.scroll is not None:
                # This scroll area is a constrained-screen fallback, not a way
                # to shrink the everyday layout. Suppressing redundant bars
                # also avoids width/height-for-width scrollbar feedback loops.
                fit_limit = window.size() if compositor_fallback else cap
                fits = wanted.width() <= fit_limit.width() and wanted.height() <= fit_limit.height()
                policy = Qt.ScrollBarPolicy.ScrollBarAlwaysOff if fits else Qt.ScrollBarPolicy.ScrollBarAsNeeded
                self.scroll.setHorizontalScrollBarPolicy(policy)
                self.scroll.setVerticalScrollBarPolicy(policy)
            # Before Wayland exposes the native frame, the undecorated cap
            # is only an upper bound. Let the compositor negotiate a persistent
            # controls floor instead of advertising complete content plus a
            # subsequently discovered title bar as its initial hard minimum.
            # A missing typed hook does not discard a visible widget's measured
            # frame. A zero frame stays unknown without the typed hook.
            visible_widget_frame = (self._workarea.enabled and handle is None
                                    and window.isVisible()
                                    and (extra_w > 0 or extra_h > 0))
            initial_frame_unknown = (available is None and self._workarea.enabled
                                     and self._normal_frame_extra is None
                                     and (handle is None or not handle.isExposed())
                                     and not visible_widget_frame)
            minimum = wanted.boundedTo(cap)
            self.preferredMinimum = wanted
            normal_mode = not (window.isMaximized() or window.isFullScreen() or window.isMinimized())
            verified_area = (available is None and self._workarea.enabled
                             and self._workarea.last_details.get("source") == "x11-ewmh")
            if normal_mode and window.isVisible() and (visible_widget_frame
                    or (handle is not None and handle.isExposed())):
                self._normal_frame_extra = QSize(extra_w, extra_h)
            normal_capacity = QSize(cap)
            basis = ("unknown" if initial_frame_unknown else
                     "current-normal-frame" if normal_mode else "maximized-frame-upper-bound")
            if not normal_mode and self._normal_frame_extra is not None:
                normal_capacity = QSize(max(1, area.width() - self._normal_frame_extra.width()),
                                        max(1, area.height() - self._normal_frame_extra.height()))
                basis = "last-normal-frame"
            self.constrained_native_maximum = bool(verified_area and (
                wanted.width() > normal_capacity.width() or wanted.height() > normal_capacity.height()))
            self.geometry_constraint = dict(
                source=self._workarea.last_details.get("source", "captured-qt"),
                reason=("preferred-minimum-exceeds-validated-workarea" if self.constrained_native_maximum
                        else "preferred-minimum-fits-validated-workarea" if verified_area
                        else self._workarea.last_details.get("reason", "workarea-unverified")),
                preferred_minimum=list(wanted.toTuple()), client_capacity=list(normal_capacity.toTuple()),
                capacity_basis=basis, available_geometry=list(area.getRect()),
            )
            if initial_frame_unknown or compositor_fallback or self.constrained_native_maximum:
                # A complete layout that cannot fit must not advertise its capped
                # height as a hard compositor minimum, even before first map.
                # Keep persistent controls and a useful scroll viewport instead.
                persistent = QSize(layout.minimumSize())
                if self.scroll is not None:
                    viewport_floor = max(self.scroll.minimumHeight(),
                                         self.scroll.minimumSizeHint().height())
                    persistent.setHeight(persistent.height() + max(0, 120 - viewport_floor))
                minimum = persistent.boundedTo(cap)
            if window.minimumSize() != minimum:
                window.setMinimumSize(minimum)
            if self.constrained_native_maximum and normal_mode and window.isVisible():
                # Wayland does not honor application positioning. When the
                # complete normal footprint cannot fit a verified work area,
                # let the compositor place the window and keep actions visible.
                window.showMaximized()
                self._workarea.refresh()
                return
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
            if not getattr(self, "_stopped", False):
                self._geometry_diagnostics_timer.start(100)

    def _publish_geometry(self):
        if getattr(self, "_stopped", False):
            return
        app = QGuiApplication.instance()
        current = app.property("studioAppearanceDiagnostics")
        if not isinstance(current, dict):
            return
        from .appearance import appearance_probes_pending
        from .system_fonts import _save_diagnostics

        def vector(value, count):
            return (list(value) if isinstance(value, (list, tuple)) and len(value) == count
                    and all(type(item) is int and abs(item) <= 16777215 for item in value) else [])

        constraint = self.geometry_constraint if isinstance(self.geometry_constraint, dict) else {}
        source = constraint.get("source")
        reason = constraint.get("reason")
        basis = constraint.get("capacity_basis")
        safe_constraint = dict(
            source=source if isinstance(source, str) and source in {"x11-ewmh", "captured-qt"} else "captured-qt",
            reason=(reason if isinstance(reason, str) and reason in {"preferred-minimum-exceeds-validated-workarea",
                                        "preferred-minimum-fits-validated-workarea"} else "workarea-unverified"),
            preferred_minimum=vector(constraint.get("preferred_minimum"), 2),
            client_capacity=vector(constraint.get("client_capacity"), 2),
            capacity_basis=(basis if isinstance(basis, str) and basis in {"current-normal-frame", "last-normal-frame",
                                              "maximized-frame-upper-bound"} else "unknown"),
            available_geometry=vector(constraint.get("available_geometry"), 4),
        )
        window = self.window
        geometry = dict(
            size=list(window.size().toTuple()), minimum=list(window.minimumSize().toTuple()),
            maximized=window.isMaximized(), minimized=window.isMinimized(), fullscreen=window.isFullScreen(),
            constrained_native_maximum=bool(self.constrained_native_maximum), geometry_constraint=safe_constraint,
            pending=dict(provider=appearance_probes_pending(app), layout=bool(self._busy), timer=self.timer.isActive()),
        )
        _save_diagnostics(app, dict(current, window_geometry=geometry))

    def _workarea_changed(self):
        if getattr(self, "_stopped", False) or not self.window.isVisible():
            return
        self.timer.start(0)

    def _screen_changed(self, *_):
        if not self._stopped:
            self._workarea.refresh()
            self.timer.start(0)

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self.timer.stop()
        self._geometry_diagnostics_timer.stop()
        self._workarea.changed.disconnect(self._workarea_changed)
        if self._screen is not None:
            self._screen.availableGeometryChanged.disconnect(self._screen_changed)
        stop_window_screen_observer(self)
        self.window.removeEventFilter(self)
