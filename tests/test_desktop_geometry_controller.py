"""Portable controller fixtures plus native Linux QProcess fault paths."""

import importlib
import json
import os
import shlex
import shutil
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, QRect, QTimer, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

HERE = Path(__file__).parent
SOURCE = (
    HERE
    if (HERE / "desktop_geometry.py").exists()
    else next((HERE.parent / "src").glob("*/desktop_geometry.py")).parent
)


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def module(tmp_path, monkeypatch):
    name = "workarea_fixture_" + uuid.uuid4().hex
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for filename in ["desktop_geometry.py", "workarea_mapping.py"]:
        shutil.copy2(SOURCE / filename, pkg / filename)
    fonts = SOURCE / "system_fonts.py"
    shutil.copy2(fonts, pkg / "system_fonts.py")
    monkeypatch.syspath_prepend(str(tmp_path))
    d = importlib.import_module(name + ".desktop_geometry")
    yield d
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            sys.modules.pop(key, None)


class App(QObject):
    aboutToQuit = Signal()

    def __init__(self, platform="wayland"):
        super().__init__()
        self.platform = platform
        self.screen = SimpleNamespace(
            geometry=lambda: QRect(0, 0, 1280, 800), devicePixelRatio=lambda: 1.0
        )

    def platformName(self):
        return self.platform

    def screens(self):
        return [self.screen]


def wait(app, predicate, timeout=3):
    until = time.monotonic() + timeout
    while not predicate() and time.monotonic() < until:
        app.processEvents()
        QTest.qWait(5)
    assert predicate()


def test_missing_helper_falls_back_safely(app, module, tmp_path, monkeypatch):
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(module, "_helper_path", lambda: tmp_path / "absent")
    parent = App()
    p = module.WorkAreaProvider(parent)
    changes = []
    p.changed.connect(lambda: changes.append(True))
    wait(app, lambda: bool(changes))
    assert p.reason == "native-helper-unavailable" and p.process is None
    assert p.available_geometry(
        SimpleNamespace(screen=lambda: parent.screen), QRect(0, 0, 1280, 800)
    ) == QRect(0, 0, 1280, 800)
    p.close()


@pytest.mark.parametrize(
    "platform,backend",
    [
        ("win32", "wayland"),
        ("darwin", "wayland"),
        ("linux", "xcb"),
        ("linux", "offscreen"),
    ],
)
def test_no_change_outside_linux_wayland(app, module, monkeypatch, platform, backend):
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(
        module,
        "_helper_path",
        lambda: pytest.fail("Disabled provider attempted a helper"),
    )
    parent = App(backend)
    p = module.WorkAreaProvider(parent)
    p.refresh()
    QTest.qWait(120)
    fallback = QRect(4, 5, 1000, 700)
    assert (
        p.available_geometry(SimpleNamespace(screen=lambda: parent.screen), fallback)
        == fallback
    )
    assert (
        p.reason == "native-qt-platform"
        and p.process is None
        and not p.refresh_timer.isActive()
    )
    p.close()


def test_untrusted_failure_reason_is_sanitized(app, module, monkeypatch):
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    parent = App()
    p = module.WorkAreaProvider(parent)
    p.refresh_timer.stop()
    p.raw = {"schema": 1, "workarea": "/home/private/person/file"}
    p.reason = None
    rect = p.available_geometry(
        SimpleNamespace(screen=lambda: parent.screen), QRect(0, 0, 1280, 800)
    )
    assert rect == QRect(0, 0, 1280, 800)
    diagnostic = parent.property("studioWorkAreaDiagnostics")
    assert (
        "/home" not in diagnostic
        and "person" not in diagnostic
        and json.loads(diagnostic)["reason"] == "workarea-source-unavailable"
    )
    p.close()


@pytest.mark.skipif(
    os.name != "posix", reason="Actual native Linux QProcess fault fixtures"
)
@pytest.mark.parametrize(
    "body,reason",
    [
        ("printf '%s' '{invalid json}'", "invalid-helper-response"),
        ("kill -SEGV $$", "helper-crashed"),
        ("exec /bin/sleep 5", "helper-timeout"),
        ("printf '%17000s' x", "helper-response-too-large"),
    ],
)
def test_native_faults_leave_ui_usable_without_python(
    app, module, tmp_path, monkeypatch, body, reason
):
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    helper = tmp_path / "appearance-probe"
    helper.write_text("#!/bin/sh\n" + body + "\n")
    helper.chmod(0o700)
    assert "python" not in helper.read_text().lower()
    monkeypatch.setattr(module, "_helper_path", lambda: helper)
    monkeypatch.setattr(
        module, "TIMEOUT_MS", 40 if reason == "helper-timeout" else 1500
    )
    parent = App()
    p = module.WorkAreaProvider(parent)
    changes = []
    p.changed.connect(lambda: changes.append(True))
    beats = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: beats.append(True))
    timer.start()
    wait(app, lambda: bool(changes))
    QTest.qWait(20)
    timer.stop()
    assert (
        p.reason == reason
        and p.raw is None
        and p.process is None
        and len(changes) == 1
        and beats
    )
    clicked = []
    button = QPushButton("Still usable")
    button.clicked.connect(lambda: clicked.append(True))
    button.click()
    assert clicked
    button.close()
    p.close()


@pytest.mark.skipif(os.name != "posix", reason="Actual host-environment native fixture")
def test_host_libraries_restored_and_python_gi_removed(
    app, module, tmp_path, monkeypatch
):
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    marker = tmp_path / "host-env"
    helper = tmp_path / "appearance-probe"
    body = """#!/bin/sh
[ "$1" = "--workarea" ] || exit 4
[ "$LD_LIBRARY_PATH" = "/host-libraries" ] || exit 5
[ -z "${PYTHONHOME:-}" ] && [ -z "${PYTHONPATH:-}" ] && [ -z "${GI_TYPELIB_PATH:-}" ] || exit 6
printf 'restored' > MARKER
printf '%s' '{"schema":1,"workarea":null,"errors":["x11-display-unavailable"]}'
""".replace("MARKER", shlex.quote(str(marker)))
    helper.write_text(body)
    helper.chmod(0o700)
    for key, value in {
        "LD_LIBRARY_PATH": "/bundled-app",
        "LD_LIBRARY_PATH_ORIG": "/host-libraries",
        "PYTHONHOME": "/bundled-python",
        "PYTHONPATH": "/bundled-modules",
        "GI_TYPELIB_PATH": "/bundled-gi",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(module, "_helper_path", lambda: helper)
    parent = App()
    p = module.WorkAreaProvider(parent)
    wait(app, lambda: p.process is None and p.reason != "workarea-probe-pending")
    assert p.reason == "x11-display-unavailable" and marker.read_text() == "restored"
    p.close()


@pytest.mark.skipif(
    os.name != "posix", reason="Actual refresh-coalescing native fixture"
)
def test_refreshes_coalesce_one_followup(app, module, tmp_path, monkeypatch):
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    marker = tmp_path / "starts"
    helper = tmp_path / "appearance-probe"
    helper.write_text(
        "#!/bin/sh\nprintf x >> "
        + shlex.quote(str(marker))
        + '\n/bin/sleep .2\nprintf \'%s\' \'{"schema":1,"workarea":null,"errors":["x11-display-unavailable"]}\'\n'
    )
    helper.chmod(0o700)
    monkeypatch.setattr(module, "_helper_path", lambda: helper)
    parent = App()
    p = module.WorkAreaProvider(parent)
    wait(app, lambda: marker.exists())
    for _ in range(30):
        p.refresh()
    wait(app, lambda: marker.read_text() == "xx" and p.process is None, timeout=4)
    QTest.qWait(250)
    assert marker.read_text() == "xx"
    p.close()
