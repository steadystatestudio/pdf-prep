"""Safe native screen hooks with widget-event fallbacks for Qt wrapper reuse."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QGuiApplication, QWindow
from shiboken6 import isValid


def _valid_window(handle):
    # A stale non-QObject layout-item wrapper can survive native deletion and
    # be returned for a reused QWindow address. isValid() alone accepts it.
    return isinstance(handle, QWindow) and isValid(handle)


class WindowScreenObserver(QObject):
    """Never connect a native signal through an unverified binding wrapper."""

    MAX_ATTEMPTS = 3
    RETRY_MS = 50

    def __init__(self, window, callback, owner):
        super().__init__(owner)
        self.window = window
        self.callback = callback
        self._handle = None
        self._attempts = 0
        self._stopped = False
        self.details = {}
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(self.request)
        self.change_timer = QTimer(self)
        self.change_timer.setSingleShot(True)
        self.change_timer.timeout.connect(callback)
        window.installEventFilter(self)

    def _record(self, state, reason):
        details = {"state": state, "reason": reason, "attempt": self._attempts,
                   "fallback": "widget-screen-events"}
        if details == self.details:
            return
        self.details = details
        app = QGuiApplication.instance()
        if app is None:
            return
        previous = app.property("studioNativeWindowHookDiagnostics")
        failures = previous.get("failures_seen", 0) if isinstance(previous, dict) else 0
        if type(failures) is not int or not 0 <= failures <= 1000000:
            failures = 0
        if state == "fallback":
            failures = min(1000000, failures + 1)
        summary = dict(details, failures_seen=failures)
        app.setProperty("studioNativeWindowHookDiagnostics", summary)
        current = app.property("studioAppearanceDiagnostics")
        if isinstance(current, dict):
            from .system_fonts import _save_diagnostics
            _save_diagnostics(app, dict(current, native_window_hook=summary))

    def request(self):
        if getattr(self, "_stopped", True) or not isValid(self.window):
            return None
        try:
            handle = self.window.windowHandle()
        except RuntimeError:
            handle = None
        if _valid_window(handle):
            if handle is not self._handle:
                self._disconnect()
                handle.screenChanged.connect(self._changed)
                self._handle = handle
            self.retry_timer.stop()
            self._record("connected", "native-qwindow")
            return handle
        self._disconnect()
        reason = "native-window-unavailable" if handle is None else "native-window-wrapper-unavailable"
        if self.window.isVisible() and self._attempts < self.MAX_ATTEMPTS:
            self._attempts += 1
            self._record("fallback", reason)
            if self._attempts < self.MAX_ATTEMPTS:
                self.retry_timer.start(self.RETRY_MS)
        return None

    def _disconnect(self):
        if _valid_window(self._handle):
            try:
                self._handle.screenChanged.disconnect(self._changed)
            except (RuntimeError, TypeError):
                pass
        self._handle = None

    def _changed(self, *_):
        if (not getattr(self, "_stopped", True) and isValid(self.window)
                and self.window.isVisible()):
            self.change_timer.start(0)

    def eventFilter(self, watched, event):
        # Qt can deliver destruction events after cyclic Python attributes
        # have been cleared; the event filter must be inert during teardown.
        if getattr(self, "_stopped", True) or not isValid(self.window):
            return False
        kind = event.type()
        if kind == QEvent.Type.Hide:
            self.retry_timer.stop()
            self.change_timer.stop()
        elif kind in {QEvent.Type.Show, QEvent.Type.WinIdChange}:
            self._attempts = 0
            self.retry_timer.start(0)
            self._changed()
        elif kind in {QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.DevicePixelRatioChange,
                      QEvent.Type.WindowStateChange}:
            self._changed()
        return False

    def stop(self):
        self._stopped = True
        self.retry_timer.stop()
        self.change_timer.stop()
        self._disconnect()
        if isValid(self.window):
            self.window.removeEventFilter(self)


def observe_window_screen(window, callback, *, owner=None):
    """Return only a verified QWindow; keep fallback fitting independent of it."""
    owner = window if owner is None else owner
    observer = getattr(owner, "_studio_screen_observer", None)
    if observer is None:
        observer = WindowScreenObserver(window, callback, owner)
        owner._studio_screen_observer = observer
    return observer.request()


def stop_window_screen_observer(owner):
    observer = getattr(owner, "_studio_screen_observer", None)
    if observer is not None:
        observer.stop()
