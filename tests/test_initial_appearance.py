"""Startup must settle initial providers before UI queues any recovery work."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

PACKAGE = next(path.parent.name for path in (Path(__file__).parents[1] / "src").glob("*/appearance.py"))
APPEARANCE = importlib.import_module(PACKAGE + ".appearance")
GEOMETRY = importlib.import_module(PACKAGE + ".desktop_geometry")
FONTS = importlib.import_module(PACKAGE + ".system_fonts")


@pytest.fixture
def startup(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    state = SimpleNamespace(app=app, pending=True, configured=[])
    monkeypatch.delattr(app, "_studio_initial_appearance_result", raising=False)
    monkeypatch.setattr(APPEARANCE, "configure_appearance", lambda instance, settings, **kw:
                        state.configured.append((instance, settings, kw)))
    monkeypatch.setattr(APPEARANCE, "appearance_probes_pending", lambda instance: state.pending)
    monkeypatch.setattr(GEOMETRY, "workarea_provider", lambda instance: None)
    monkeypatch.setattr(FONTS, "polish_system_text", lambda instance: None)
    state.settings = QSettings(str(tmp_path / "startup.ini"), QSettings.IniFormat)
    state.settings.setFallbacksEnabled(False)
    return state


def test_initial_ready_phase_precedes_factory_and_queued_recovery(startup):
    order = []
    QTimer.singleShot(25, lambda: (order.append("provider-ready"), setattr(startup, "pending", False)))
    result = APPEARANCE.prepare_initial_appearance(startup.app, startup.settings, timeout_ms=300)
    assert result["ready"] and order == ["provider-ready"]

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            order.append("construct")
            central = QWidget(self)
            QVBoxLayout(central)
            self.setCentralWidget(central)
            self.window_size_guard = SimpleNamespace(update=lambda: order.append("fit"))
            QTimer.singleShot(0, lambda: order.append("recovery-after-show" if self.isVisible()
                                                     else "recovery-before-show"))

    window = Window()  # Strong local lifetime is retained throughout preparation/show.
    try:
        APPEARANCE.prepare_window_for_show(window, lambda: order.append("arrange"))
        assert order == ["provider-ready", "construct"] + ["arrange", "fit"] * 3
        window.show()
        startup.app.processEvents()
        assert order[-1] == "recovery-after-show"
    finally:
        window.close()


def test_unavailable_providers_have_one_bounded_honest_fallback(startup):
    began = time.monotonic()
    result = APPEARANCE.prepare_initial_appearance(startup.app, startup.settings, timeout_ms=40)
    assert .02 <= time.monotonic() - began < 1.5
    assert result == dict(ready=False, reason="initial-providers-pending-at-deadline", deadline_ms=40)
    queued = []
    QTimer.singleShot(0, lambda: queued.append("later-refresh"))
    # A later configure/refresh cannot pump events, rebuild UI or repeat the wait.
    assert APPEARANCE.prepare_initial_appearance(startup.app, startup.settings, timeout_ms=300) == result
    assert queued == []
    startup.app.processEvents()
    assert queued == ["later-refresh"]


def test_exact_settings_and_explicit_override_keys_are_preserved(startup):
    startup.pending = False
    values = {"ui/appearance": "dark", "appearance/font_mode": "custom",
              "appearance/font_family": "Sans Serif", "appearance/font_size_pt": 18.0,
              "appearance/accent_mode": "custom", "appearance/accent": "#d34f91",
              "recursive": True}
    for key, value in values.items():
        startup.settings.setValue(key, value)
    before = {key: startup.settings.value(key) for key in startup.settings.allKeys()}
    result = APPEARANCE.prepare_initial_appearance(startup.app, startup.settings, theme_key="ui/appearance")
    assert result["ready"]
    assert startup.configured == [(startup.app, startup.settings, {"theme_key": "ui/appearance"})]
    assert {key: startup.settings.value(key) for key in startup.settings.allKeys()} == before


def test_real_override_is_applied_before_factory_even_when_integrations_fail(tmp_path):
    # A fresh process exercises the real resolver without disturbing a running
    # app/test's native capture, active watchers, settings or font baseline.
    script = '''
import importlib,json,os,sys
from pathlib import Path
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFontDatabase,QFontInfo
from PySide6.QtWidgets import QApplication,QMainWindow
appearance=importlib.import_module(sys.argv[1])
app=QApplication([])
app.setApplicationName("Studio Startup Test")
app.setOrganizationName("Studio Tests")
if os.name=="nt":
 font_path=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts/segoeui.ttf"
 if font_path.is_file():QFontDatabase.addApplicationFont(str(font_path))
family=QFontDatabase.families()[0]
settings=QSettings(sys.argv[2],QSettings.IniFormat)
settings.setFallbacksEnabled(False)
for key,value in {"theme":"dark","appearance/font_mode":"custom",
                  "appearance/font_family":family,"appearance/font_size_pt":18.0,
                  "appearance/accent_mode":"custom","appearance/accent":"#d34f91"}.items():
 settings.setValue(key,value)
before={key:settings.value(key) for key in settings.allKeys()}
for name in ("gtk","portal"):
 def fail(app,generation,name=name):
  appearance._provider_finished(app,generation,name,{"reason":"fixture-integration-unavailable"})
 setattr(appearance,"_start_native" if name=="gtk" else "_start_portal",fail)
ready=appearance.prepare_initial_appearance(app,settings,theme_key="theme",timeout_ms=500)
window=QMainWindow()
appearance.prepare_window_for_show(window)
print(json.dumps(dict(ready=ready,font=window.font().pointSizeF(),actual=QFontInfo(window.font()).pointSizeF(),
    accent=app._studio_appearance_values["accent"],scheme=app._studio_appearance_values["color_scheme"],
    source=app._studio_appearance_resolution["font"]["source"],
    same_settings=app._studio_pilot_settings is settings,
    preserved=before=={key:settings.value(key) for key in settings.allKeys()})))
window.close()
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    src = str(Path(APPEARANCE.__file__).parents[1])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", script, APPEARANCE.__name__, str(tmp_path / "real.ini")],
                            env=env, text=True, capture_output=True, timeout=10, check=True)
    observed = json.loads(result.stdout)
    assert observed["ready"]["ready"] and observed["font"] == observed["actual"] == 18
    assert observed["accent"] == "#d34f91" and observed["scheme"] == "dark"
    assert observed["source"] == "app-override" and observed["same_settings"] and observed["preserved"]


@pytest.mark.parametrize("state", [Qt.WindowMaximized, Qt.WindowFullScreen, Qt.WindowMinimized])
def test_synchronous_prefit_preserves_saved_window_state_without_dispatch(startup, state):
    window = QMainWindow()
    called = []
    window.window_size_guard = SimpleNamespace(update=lambda: called.append("fit"))
    window.setWindowState(state)
    QTimer.singleShot(0, lambda: called.append("autoload"))
    try:
        APPEARANCE.prepare_window_for_show(window)
        assert window.windowState() == state
        assert called == ["fit"] * 3
    finally:
        # Drain the harmless test callback while its window/local state is alive.
        startup.app.processEvents()
        window.close()
