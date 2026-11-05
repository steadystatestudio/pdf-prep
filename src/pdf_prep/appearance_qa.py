"""Explicit package diagnostics; invoked only by the existing smoke checks."""

import json
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QSpinBox,
    QTextEdit,
    QWidget,
)

from .appearance import (
    appearance_details,
    appearance_probes_pending,
    configure_appearance,
)
from .desktop_geometry import available_geometry, workarea_probes_pending
from .qa_file_dialog import inspect_file_dialog
from .system_fonts import polish_system_text
from .window_screen import _valid_window


def _geometry_mode(window):
    guard = getattr(window, "window_size_guard", None)
    constrained = bool(guard is not None and getattr(guard, "constrained_native_maximum", False))
    details = dict(getattr(guard, "geometry_constraint", {})) if guard is not None else {}
    if constrained:
        assert details.get("source") == "x11-ewmh", "Unverified constrained window mode"
    return ("constrained-native-max" if constrained else "normal"), details


def _qa_window_handle(window):
    try:
        handle = window.windowHandle()
    except RuntimeError:
        return None
    return handle if _valid_window(handle) else None


def _request_qa_state(window, maximized):
    """Synchronize the QWidget and verified QWindow before testing geometry."""
    method = "showMaximized" if maximized else "showNormal"
    getattr(window, method)()
    handle = _qa_window_handle(window)
    if handle is not None:
        getattr(handle, method)()


def _prepare_qa_normal_state(app, window):
    if _geometry_mode(window)[0] != "normal":
        return
    # QWidget can already report Normal while its QWindow and the real X11 WM
    # remain maximized. A bounded explicit transition repairs that precondition;
    # it does not assist subsequent minimum-size or persistent-control checks.
    _request_qa_state(window, True)
    _settle_geometry(app, window, normal=False)
    if _geometry_mode(window)[0] == "normal":
        _request_qa_state(window, False)
        _settle_geometry(app, window)


def _qa_resize_request(window, width, area):
    frame = window.frameGeometry()
    capacity_width = max(1, area.width() - max(0, frame.width() - window.width()))
    capacity_height = max(1, area.height() - max(0, frame.height() - window.height()))
    return min(width, capacity_width), min(800, capacity_height)


def _qa_footprint_fits(window, frame, area, *, intermediate_maximum=False):
    if intermediate_maximum:
        # Windows keeps invisible resize borders outside the work area while
        # maximized. This transition checks the client before returning Normal;
        # all final normal/constrained geometry checks still use the full frame.
        return window.width() <= area.width() + 2 and window.height() <= area.height() + 2
    return frame.width() <= area.width() + 2 and frame.height() <= area.height() + 2


def _settle_geometry(app, window, *, normal=True, timeout=5):
    """Wait for strict geometry invariants and native configure acknowledgements."""
    deadline = time.monotonic() + timeout
    previous, stable, requested_state = None, 0, None
    while time.monotonic() < deadline:
        app.processEvents()
        raw = window.screen().availableGeometry()
        area = available_geometry(window, raw)
        mode, _ = _geometry_mode(window)
        desired_state = "max" if mode == "constrained-native-max" or not normal else "normal"
        if desired_state != requested_state:
            if desired_state is not None:
                _request_qa_state(window, desired_state == "max")
            requested_state = desired_state
        handle = _qa_window_handle(window)
        native_state = handle.windowState() if handle is not None else None
        frame = window.frameGeometry()
        row = getattr(window, "action_row", None)
        signature = (
            window.size().toTuple(),
            window.minimumSize().toTuple(),
            frame.getRect(),
            area.getRect(),
            window.fontInfo().pixelSize(),
            window.isMaximized(),
            str(native_state) if native_state is not None else "unavailable",
            mode,
            None if row is None else (row.height(), row.minimumSizeHint().toTuple()),
        )
        timers = [getattr(window, name, None) for name in ("layout_timer", "fit_timer")]
        timers.append(getattr(getattr(window, "window_size_guard", None), "timer", None))
        if row is not None:
            timers.append(getattr(row, "metrics_timer", None))
        valid = (
            not appearance_probes_pending(app)
            and not workarea_probes_pending(app)
            and window.isMaximized() == (mode == "constrained-native-max" or not normal)
            and (native_state is None or bool(native_state & Qt.WindowState.WindowMaximized) == window.isMaximized())
            and (
                native_state is None or not normal or mode == "constrained-native-max"
                or not native_state & (
                    Qt.WindowState.WindowMaximized | Qt.WindowState.WindowMinimized | Qt.WindowState.WindowFullScreen
                )
            )
            and window.width() >= window.minimumWidth()
            and window.height() >= window.minimumHeight()
            and _qa_footprint_fits(window, frame, area,
                                  intermediate_maximum=not normal and mode == "normal")
            and not any(timer and timer.isActive() for timer in timers)
            and (row is None or row.height() == row.height_for_width(row.width()))
        )
        stable = stable + 1 if valid and signature == previous else 0
        if stable >= 6:
            return
        previous = signature
        time.sleep(0.02)
    raise AssertionError(
        (
            "geometry-did-not-settle",
            signature,
            {"font_probe": appearance_probes_pending(app), "workarea_probe": workarea_probes_pending(app)},
        )
    )


def _persistent_workspace(window, area):
    """Check visible actions/status and an actual work viewport without reading text."""
    workspace = getattr(window, "work_scroll", None) or getattr(window, "splitter", None)
    assert isinstance(workspace, QWidget), "Missing working surface"
    persistent = []
    seen = set()
    names = (
        "rename",
        "undo",
        "process_btn",
        "save_photo_btn",
        "export_button",
        "prepare_button",
        "save_button",
        "extract_button",
        "cancel_button",
        "open_button",
        "new_button",
        "open_folder_button",
        "progress",
        "progress_bar",
        "status",
        "status_label",
        "add_files",
        "add_folder",
        "clear",
        "add_btn",
        "add_folder_btn",
        "clear_btn",
        "help_btn",
        "help_button",
        "settings_btn",
        "settings_button",
        "donate_btn",
        "donate_button",
    )
    for name in names:
        widget = getattr(window, name, None)
        if not isinstance(widget, QWidget) or not widget.isVisible() or id(widget) in seen:
            continue
        # Scrollable configuration controls are clipped by their viewport; task
        # actions outside that content must remain visible at every work-area size.
        if workspace.isAncestorOf(widget):
            continue
        seen.add(id(widget))
        rect = QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())
        assert window.rect().contains(rect), ("persistent-control-outside-client", name, rect.getRect())
        persistent.append((name, widget, rect))
    assert any(isinstance(widget, QAbstractButton) for _, widget, _ in persistent), "Missing persistent task action"
    assert any(isinstance(widget, (QLabel, QProgressBar)) for _, widget, _ in persistent), "Missing persistent status"
    for index, (name, widget, rect) in enumerate(persistent):
        for other_name, other, other_rect in persistent[index + 1 :]:
            if widget.isAncestorOf(other) or other.isAncestorOf(widget):
                continue
            assert not rect.intersects(other_rect), ("persistent-controls-overlap", name, other_name)
    viewport = workspace.viewport() if hasattr(workspace, "viewport") else workspace
    rect = QRect(viewport.mapTo(window, QPoint(0, 0)), viewport.size()).intersected(window.rect())
    reserve = min(max(1, window.fontMetrics().height() * 3), max(1, area.height() // 4))
    assert rect.width() > 0 and rect.height() >= reserve, ("workspace-reserve-missing", rect.getRect(), reserve)
    for name, widget, control_rect in persistent:
        if workspace.isAncestorOf(widget):
            continue
        assert not rect.intersects(control_rect), ("workspace-overlaps-persistent-control", name)
    return {
        "workspace": rect.getRect(),
        "minimum_workspace_height": reserve,
        "persistent_controls": [{"name": name, "rectangle": rect.getRect()} for name, _, rect in persistent],
    }


def capture_appearance(app, window, destination):
    """Measure native text inheritance and the real compact layout in the bundle."""
    destination = Path(destination) / "appearance"
    destination.mkdir(parents=True, exist_ok=False)
    original_state, original_size = window.windowState(), window.size()
    _settle_geometry(app, window)
    rendered_font = json.loads(appearance_details(app))["actual_font"]
    rendered_available = rendered_font["pixel_size"] > 0
    if app.platformName() not in {"offscreen", "minimal"}:
        assert rendered_available, "Native platform did not resolve a rendered UI font"
    font, size = QFont(app.font()), original_size
    original_native = app.property("studioAppearanceDiagnostics")
    settings = app._studio_pilot_settings

    def reapply_theme():
        callback = getattr(app, "_studio_refresh_appearance", None)
        if callback:
            callback()

    override_keys = ("appearance/font_mode", "appearance/font_family", "appearance/font_size_pt")
    original_overrides = {key: settings.value(key) for key in override_keys if settings.contains(key)}
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
    file_dialogs = []

    def settle():
        _settle_geometry(app, window)

    try:
        for label, points in (("native", font.pointSizeF()), ("large-layout", max(18, font.pointSizeF()))):
            enlarged = QFont(font)
            if points > 0:
                enlarged.setPointSizeF(points)
            settings.setValue("appearance/font_mode", "custom")
            settings.setValue("appearance/font_family", enlarged.family())
            settings.setValue("appearance/font_size_pt", enlarged.pointSizeF())
            configure_appearance(app, settings)
            reapply_theme()
            settle()
            assert abs(app.font().pointSizeF() - points) < 0.01, "Requested QA font was not applied"
            polish_system_text(app)
            _prepare_qa_normal_state(app, window)
            widgets = []
            for widget in window.findChildren(QWidget):
                if not isinstance(widget, surfaces) or widget.property("studioContentTypography"):
                    continue
                # Qt stylesheets resolve point sizes into pixels on some platforms.
                actual, expected = widget.fontInfo().pixelSize(), window.fontInfo().pixelSize()
                assert actual == expected, (type(widget).__name__, widget.objectName(), actual, expected)
                widgets.append({"type": type(widget).__name__, "name": widget.objectName(), "pixels": actual})
            heights = []
            for width in (1280, 960, 820, 1):
                mode, _ = _geometry_mode(window)
                area = available_geometry(window, window.screen().availableGeometry())
                applied_request = None
                if mode == "normal":
                    _request_qa_state(window, False)
                    applied_request = _qa_resize_request(window, width, area)
                    window.resize(*applied_request)
                settle()
                mode, constraint = _geometry_mode(window)
                if width == 1 and mode == "normal" and window.width() != window.minimumWidth():
                    window.resize(1, window.height())
                    settle()
                    mode, constraint = _geometry_mode(window)
                area = available_geometry(window, window.screen().availableGeometry())
                frame = window.frameGeometry()
                assert frame.width() <= area.width() + 2 and frame.height() <= area.height() + 2, (
                    label,
                    frame.getRect(),
                    area.getRect(),
                    window.minimumSize().toTuple(),
                )
                assert window.width() >= window.minimumWidth(), (
                    label,
                    width,
                    window.size().toTuple(),
                    window.minimumSize().toTuple(),
                )
                if width == 1 and mode == "normal":
                    assert window.width() == window.minimumWidth(), "Normal minimum-width clamp did not stop resizing"
                workspace = _persistent_workspace(window, area)
                if hasattr(window, "action_row") and hasattr(window, "rename"):
                    row = window.action_row
                    controls, primary, undo = row.controls, window.rename, window.undo
                    if row.width() >= row.minimumSizeHint().width():
                        assert primary.x() >= controls.geometry().right() + 8
                        assert primary.geometry().right() + 8 <= undo.x()
                        assert primary.y() == undo.y()
                        heights.append(row.height())
                records.append(
                    {
                        "font_case": label,
                        "requested_width": width,
                        "applied_resize_request": applied_request,
                        "size": window.size().toTuple(),
                        "minimum": window.minimumSize().toTuple(),
                        "font": window.font().toString(),
                        "text_surfaces": len(widgets),
                        "geometry_mode": mode,
                        "geometry_constraint": constraint,
                        "raw_qt_available_geometry": window.screen().availableGeometry().getRect(),
                        "effective_available_geometry": area.getRect(),
                        "workspace": workspace,
                    }
                )
            assert len(set(heights)) <= 1, heights
            file_dialogs.append(
                dict(
                    font_case=label,
                    **inspect_file_dialog(
                        app, window, destination, surfaces, lambda dialog: _settle_geometry(app, dialog)
                    ),
                )
            )
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

                def inspect_help(label=label, help_errors=help_errors):
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
                    except Exception as exc:  # noqa: BLE001 - Always close the modal QA dialog.
                        help_errors.append(repr(exc))
                    finally:
                        if dialog:
                            dialog.close()

                QTimer.singleShot(150, inspect_help)
                help_action()
                assert not help_errors, help_errors
        exercise_production_settings(app, window, destination)
    finally:
        for key in override_keys:
            if key in original_overrides:
                settings.setValue(key, original_overrides[key])
            else:
                settings.remove(key)
        configure_appearance(app, settings)
        reapply_theme()
        window.resize(size)
        settle()
        window.setWindowState(original_state)
        _settle_geometry(app, window, normal=False)
    (destination / "report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "native": original_native,
                "cases": records,
                "text_surfaces": widgets,
                "file_dialogs": file_dialogs,
                "rendered_font": rendered_font,
                "rendered_font_available": rendered_available,
                "font_verification_scope": "rendered metrics"
                if rendered_available
                else "requested inheritance only; offscreen font integration unavailable",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def exercise_production_settings(app, window, destination):
    """Exercise shipped controls against the isolated smoke-test preferences."""
    from PySide6.QtGui import QColor

    from .appearance import configure_appearance
    from .appearance_controls import AppearanceSettingsDialog

    settings = app._studio_pilot_settings
    theme_key = getattr(app, "_studio_appearance_theme_key", "theme")

    def keys():
        return [key for key in settings.allKeys() if key == theme_key or key.startswith("appearance/")]

    original = {key: settings.value(key) for key in keys()}
    unrelated = {key: settings.value(key) for key in settings.allKeys() if key not in original}
    callback = getattr(app, "_studio_refresh_appearance", None)
    dialogs = []
    checks = []

    def settle():
        _settle_geometry(app, window)
        for dialog in dialogs:
            if dialog.isVisible():
                _settle_geometry(app, dialog)

    def open_dialog():
        dialog = AppearanceSettingsDialog(settings, window, theme_key, callback)
        dialogs.append(dialog)
        dialog.show()
        settle()
        assert dialog.minimumSize() == dialog.maximumSize()
        assert not any("copy appearance" in b.text().lower() for b in dialog.findChildren(QAbstractButton))
        for widget in dialog.findChildren(QWidget):
            if isinstance(widget, (QAbstractButton, QComboBox, QLabel, QLineEdit, QSpinBox)):
                assert widget.fontInfo().pixelSize() == window.fontInfo().pixelSize(), type(widget).__name__
        return dialog

    try:
        dialog = open_dialog()
        assert dialog.grab().save(str(destination / "settings-system.png"))
        controls = dialog.controls
        controls.font_mode.setCurrentIndex(1)
        controls.font_family.setCurrentFont(QFont(app.font().family()))
        controls.font_size.setValue(18)
        controls.accent_mode.setCurrentIndex(1)
        controls.accent_color = QColor("#d34f91")
        controls.theme.setCurrentIndex(controls.theme.findData("dark"))
        dialog.accept()
        settle()
        assert abs(app.font().pointSizeF() - 18) < 0.01
        assert app._studio_appearance_values["accent"] == "#d34f91"
        assert app._studio_appearance_values["color_scheme"] == "dark"
        assert window.grab().save(str(destination / "custom-font-accent.png"))
        checks.append("Saved custom font and accent apply to the packaged interface")

        dialog = open_dialog()
        dialog.controls.theme.setCurrentIndex(dialog.controls.theme.findData("light"))
        dialog.accept()
        settle()
        assert abs(app.font().pointSizeF() - 18) < 0.01
        assert app._studio_appearance_values["accent"] == "#d34f91"
        assert app._studio_appearance_values["color_scheme"] == "light"
        checks.append("Theme changes retain custom font and accent")

        dialog = open_dialog()
        dialog.controls.font_size.setValue(23)
        dialog.controls.refresh_appearance_button.click()
        settle()
        assert dialog.controls.font_size.value() == 23
        assert settings.value("appearance/font_size_pt", type=float) == 18
        dialog.reject()
        assert settings.value("appearance/font_size_pt", type=float) == 18
        checks.append("Refresh retains staged choices and Cancel preserves saved preferences")

        dialog = open_dialog()
        dialog.controls.reset_appearance_button.click()
        assert settings.value("appearance/font_mode") == "custom"
        dialog.accept()
        settle()
        assert settings.value("appearance/font_mode") == "system"
        assert settings.value(theme_key) == "system"
        assert {key: settings.value(key) for key in unrelated} == unrelated
        checks.append("Appearance-only Reset preserves unrelated preferences")
    finally:
        for dialog in dialogs:
            dialog.close()
        for key in keys():
            settings.remove(key)
        for key, value in original.items():
            settings.setValue(key, value)
        settings.sync()
        configure_appearance(app, settings, theme_key=theme_key)
        if callback:
            callback()
        settle()
    (destination / "settings.json").write_text(
        json.dumps({"passed": True, "checks": checks}, indent=2), encoding="utf-8"
    )
