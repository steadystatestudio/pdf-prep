"""Optional validated desktop work areas, independent of font/theme providers."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QRect, QTimer, Signal

from .system_fonts import host_environment
from .workarea_mapping import normalize_workarea

MAX_OUTPUT = 16384
TIMEOUT_MS = 2000
NATIVE_FAILURES = {
    "x11-library-unavailable", "x11-api-unavailable", "x11-display-unavailable",
    "x11-root-geometry-invalid", "ewmh-workarea-unavailable-or-invalid",
    "ewmh-workarea-outside-root", "ewmh-current-desktop-invalid",
    "ewmh-current-desktop-unavailable",
}


def _helper_path():
    packaged = Path(__file__).resolve().parent / "assets/appearance-probe"
    return packaged if packaged.is_file() else Path(__file__).resolve().parents[2] / "build/native/appearance-probe"


class WorkAreaProvider(QObject):
    changed = Signal()

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.raw = None
        self.process = None
        self.reason = "workarea-probe-pending"
        self.last_details = dict(source="captured-qt", reason="workarea-not-yet-resolved")
        self.enabled = sys.platform.startswith("linux") and app.platformName().startswith("wayland")
        self.pending = False
        self.closing = False
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self._start)
        self.app.aboutToQuit.connect(self.close)
        if self.enabled:
            self.refresh_timer.start(0)
        else:
            self.reason = "native-qt-platform"

    def refresh(self):
        if not self.enabled or self.closing:
            return
        self.pending = True
        self.refresh_timer.start(100)

    def is_pending(self):
        return self.enabled and not self.closing and (self.process is not None or self.refresh_timer.isActive())

    def close(self):
        self.closing = True
        self.refresh_timer.stop()
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(100)

    def _start(self):
        if self.closing or not self.enabled:
            return
        if self.process is not None:
            self.pending = True
            return
        self.pending = False
        helper = _helper_path()
        if not helper.is_file():
            self.raw, self.reason = None, "native-helper-unavailable"
            self.changed.emit()
            return
        process = QProcess(self)
        self.process = process
        timer = QTimer(process)
        timer.setSingleShot(True)
        environment = QProcessEnvironment()
        for key, value in host_environment().items():
            environment.insert(key, value)
        process.setProcessEnvironment(environment)
        process.setStandardErrorFile(os.devnull)
        process.setProgram(str(helper))
        process.setArguments(["--workarea"])
        state = {"buffer": bytearray(), "done": False}

        def complete(reason=None):
            if state["done"]:
                return
            state["done"] = True
            timer.stop()
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
            self.process = None
            self.raw = None
            self.reason = reason
            if reason is None:
                try:
                    data = json.loads(bytes(state["buffer"]))
                    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
                        raise ValueError
                    if data.get("workarea") is None:
                        errors = data.get("errors")
                        self.reason = (errors[0] if isinstance(errors, list) and len(errors) == 1
                                       and errors[0] in NATIVE_FAILURES else "native-workarea-unavailable")
                    else:
                        self.raw = data
                except (ValueError, UnicodeError, TypeError):
                    self.reason = "invalid-helper-response"
            if not self.closing:
                self.changed.emit()
            process.deleteLater()
            if self.pending and not self.closing:
                self.refresh_timer.start(100)

        def read():
            if state["done"]:
                return
            state["buffer"].extend(bytes(process.read(MAX_OUTPUT + 1 - len(state["buffer"]))))
            if len(state["buffer"]) > MAX_OUTPUT:
                state["buffer"] = state["buffer"][:MAX_OUTPUT]
                complete("helper-response-too-large")

        def finished(code, status):
            read()
            complete(None if code == 0 and status == QProcess.ExitStatus.NormalExit else "helper-failed")

        def failed(error):
            reason = (
                "helper-crashed" if error == QProcess.ProcessError.Crashed else
                "helper-process-unavailable" if error == QProcess.ProcessError.FailedToStart else
                "helper-process-error"
            )
            complete(reason)

        process.readyReadStandardOutput.connect(read)
        process.finished.connect(finished)
        process.errorOccurred.connect(failed)
        timer.timeout.connect(lambda: complete("helper-timeout"))
        timer.start(TIMEOUT_MS)
        process.start()

    def available_geometry(self, window, fallback):
        if not self.enabled or self.raw is None:
            details = dict(source="captured-qt", reason=self.reason or "native-workarea-unavailable")
            result = QRect(fallback)
        else:
            screens = self.app.screens()
            try:
                target = screens.index(window.screen())
            except ValueError:
                target = -1
            mapped, details = normalize_workarea(
                self.raw,
                [dict(geometry=screen.geometry().getRect(), dpr=screen.devicePixelRatio()) for screen in screens],
                target,
                os.environ.get("QT_SCALE_FACTOR"),
            )
            result = QRect(*mapped).intersected(fallback) if mapped is not None else QRect(fallback)
            if result.isEmpty():
                result, details = QRect(fallback), dict(source="captured-qt", reason="workarea-intersection-empty")
        details["available_geometry"] = list(result.getRect())
        self.last_details = details
        self.app.setProperty("studioWorkAreaDiagnostics", json.dumps(details, sort_keys=True))
        from .system_fonts import update_workarea_diagnostics
        update_workarea_diagnostics(self.app, details)
        return result


def workarea_provider(app):
    provider = getattr(app, "_studio_workarea_provider", None)
    if provider is None:
        provider = WorkAreaProvider(app)
        app._studio_workarea_provider = provider
    return provider


def available_geometry(window, fallback):
    from PySide6.QtWidgets import QApplication
    return workarea_provider(QApplication.instance()).available_geometry(window, fallback)


def workarea_probes_pending(app):
    provider = getattr(app, "_studio_workarea_provider", None)
    return bool(provider is not None and provider.is_pending())


def workarea_details(app):
    provider = getattr(app, "_studio_workarea_provider", None)
    if provider is None:
        return dict(source="captured-qt", reason="workarea-not-yet-resolved")
    return dict(provider.last_details)
