"""Explicit package diagnostics; invoked only by the existing smoke checks."""

import json
import time
from pathlib import Path

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

from .appearance import appearance_details, appearance_probes_pending, configure_appearance
from .system_fonts import polish_system_text


def capture_appearance(app, window, destination):
    """Measure native text inheritance and the real compact layout in the bundle."""
    destination = Path(destination) / "appearance"
    destination.mkdir(parents=True, exist_ok=False)
    probe_deadline = time.monotonic() + 5
    while appearance_probes_pending(app) and time.monotonic() < probe_deadline:
        app.processEvents()
        time.sleep(.005)
    assert not appearance_probes_pending(app), "Desktop appearance probes did not settle"
    rendered_font = json.loads(appearance_details(app))["actual_font"]
    rendered_available = rendered_font["pixel_size"] > 0
    if app.platformName() not in {"offscreen", "minimal"}:
        assert rendered_available, "Native platform did not resolve a rendered UI font"
    font, size = QFont(app.font()), window.size()
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
            settings.setValue("appearance/font_mode", "custom")
            settings.setValue("appearance/font_family", enlarged.family())
            settings.setValue("appearance/font_size_pt", enlarged.pointSizeF())
            configure_appearance(app, settings)
            reapply_theme()
            settle()
            assert abs(app.font().pointSizeF() - points) < .01, "Requested QA font was not applied"
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
    (destination / "report.json").write_text(
        json.dumps(
            dict(passed=True, native=original_native, cases=records, text_surfaces=widgets,
                 rendered_font=rendered_font, rendered_font_available=rendered_available,
                 font_verification_scope="rendered metrics" if rendered_available
                 else "requested inheritance only; offscreen font integration unavailable"),
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
        deadline = time.monotonic() + .2
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)

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
        assert abs(app.font().pointSizeF() - 18) < .01
        assert app._studio_appearance_values["accent"] == "#d34f91"
        assert app._studio_appearance_values["color_scheme"] == "dark"
        assert window.grab().save(str(destination / "custom-font-accent.png"))
        checks.append("Saved custom font and accent apply to the packaged interface")

        dialog = open_dialog()
        dialog.controls.theme.setCurrentIndex(dialog.controls.theme.findData("light"))
        dialog.accept()
        settle()
        assert abs(app.font().pointSizeF() - 18) < .01
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
