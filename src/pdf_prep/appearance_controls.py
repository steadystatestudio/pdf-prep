"""Shared staged appearance controls; host detection remains optional and local."""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class AppearanceControls(QWidget):
    """Stage choices until save_settings(); Refresh never clears staged inputs."""

    def __init__(self, settings, parent=None, theme_key="theme", apply_callback=None):
        super().__init__(parent)
        self.settings = settings
        self.theme_key = theme_key
        self.apply_callback = apply_callback
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.theme = QComboBox()
        for label, value in (("System", "system"), ("Light", "light"), ("Dark", "dark")):
            self.theme.addItem(label, value)
        self._select(self.theme, settings.value(theme_key, "system"))
        form.addRow("Appearance", self.theme)
        self.font_mode = QComboBox()
        self.font_mode.addItem("Use system font", "system")
        self.font_mode.addItem("Use custom font", "custom")
        self._select(self.font_mode, settings.value("appearance/font_mode", "system"))
        form.addRow("Interface text", self.font_mode)
        self.font_family = QFontComboBox()
        self.font_family.setCurrentFont(QFont(str(settings.value("appearance/font_family", self.font().family()))))
        form.addRow("Font", self.font_family)
        self.font_size = QDoubleSpinBox()
        self.font_size.setRange(6, 72)
        self.font_size.setSingleStep(.5)
        self.font_size.setSuffix(" pt")
        try:
            points = float(settings.value("appearance/font_size_pt", self.font().pointSizeF()))
        except (TypeError, ValueError):
            points = 10
        self.font_size.setValue(points if math.isfinite(points) and 6 <= points <= 72 else 10)
        form.addRow("Size", self.font_size)
        self.accent_mode = QComboBox()
        self.accent_mode.addItem("Use system accent", "system")
        self.accent_mode.addItem("Use custom accent", "custom")
        self._select(self.accent_mode, settings.value("appearance/accent_mode", "system"))
        form.addRow("Action color", self.accent_mode)
        color = QColor(str(settings.value("appearance/accent", "#2768ad")))
        self.accent_color = color if color.isValid() and color.alpha() == 255 else QColor("#2768ad")
        self.accent_button = QPushButton()
        self.accent_button.clicked.connect(self._choose_accent)
        self._update_accent_label()
        form.addRow("Accent", self.accent_button)
        layout.addLayout(form)
        self.refresh_appearance_button = QPushButton("Refresh system settings")
        self.reset_appearance_button = QPushButton("Reset appearance to System")
        self.refresh_appearance_button.clicked.connect(self._refresh_appearance)
        self.reset_appearance_button.clicked.connect(self._reset_appearance)
        layout.addWidget(self.refresh_appearance_button)
        layout.addWidget(self.reset_appearance_button)
        self.appearance_note = QLabel("Custom choices take effect when you save.")
        self.appearance_note.setWordWrap(True)
        layout.addWidget(self.appearance_note)
        self.font_mode.currentIndexChanged.connect(self._update_appearance_controls)
        self.accent_mode.currentIndexChanged.connect(self._update_appearance_controls)
        self._update_appearance_controls()

    @staticmethod
    def _select(control, value):
        index = control.findData(str(value).lower())
        control.setCurrentIndex(max(0, index))

    def _update_appearance_controls(self):
        custom = self.font_mode.currentData() == "custom"
        self.font_family.setEnabled(custom)
        self.font_size.setEnabled(custom)
        self.accent_button.setEnabled(self.accent_mode.currentData() == "custom")

    def _update_accent_label(self):
        self.accent_button.setText(f"Choose color ({self.accent_color.name()})")
        self.accent_button.setAccessibleName(f"Choose custom accent color, {self.accent_color.name()}")

    def _choose_accent(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Choose accent color")
        dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        dialog.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        picker = QColorDialog(self.accent_color, dialog)
        picker.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
        picker.setOption(QColorDialog.ColorDialogOption.NoButtons)
        picker.setWindowFlags(Qt.WindowType.Widget)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(picker)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(scroll)
        layout.addWidget(buttons)
        picker.ensurePolished()
        picker.adjustSize()
        area = dialog.screen().availableGeometry()
        margins = layout.contentsMargins()
        dialog.setFixedSize(min(area.width() - 32, picker.sizeHint().width() + margins.left() + margins.right() + 4),
                            min(area.height() - 64, picker.sizeHint().height() + buttons.sizeHint().height()
                                + margins.top() + margins.bottom() + layout.spacing() + 4))
        color = picker.currentColor() if dialog.exec() == QDialog.Accepted else QColor()
        if color.isValid():
            self.accent_color = QColor(color.name())
            self._update_accent_label()

    def _reset_appearance(self):
        self._select(self.theme, "system")
        self._select(self.font_mode, "system")
        self._select(self.accent_mode, "system")
        self.appearance_note.setText("Save to use system appearance. Other settings are retained.")

    def _refresh_appearance(self):
        from .appearance import configure_appearance
        configure_appearance(QApplication.instance(), self.settings, refresh=True, theme_key=self.theme_key)
        if self.apply_callback:
            self.apply_callback()
        self.appearance_note.setText("System settings refreshed. Your unsaved choices are preserved.")

    def save_settings(self):
        self.settings.setValue(self.theme_key, self.theme.currentData())
        self.settings.setValue("appearance/font_mode", self.font_mode.currentData())
        if self.font_mode.currentData() == "custom":
            self.settings.setValue("appearance/font_family", self.font_family.currentFont().family())
            self.settings.setValue("appearance/font_size_pt", self.font_size.value())
        else:
            self.settings.remove("appearance/font_family")
            self.settings.remove("appearance/font_size_pt")
        self.settings.setValue("appearance/accent_mode", self.accent_mode.currentData())
        if self.accent_mode.currentData() == "custom":
            self.settings.setValue("appearance/accent", self.accent_color.name())
        else:
            self.settings.remove("appearance/accent")
        self.settings.sync()


class AppearanceSettingsDialog(QDialog):
    """Fitted form with scrolling only when the monitor cannot fit the form."""

    def __init__(self, settings, parent=None, theme_key="theme", apply_callback=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        self.setSizeGripEnabled(False)
        self.settings = settings
        self.theme_key = theme_key
        self.apply_callback = apply_callback
        self.content_layout = QVBoxLayout()
        self.body = QWidget()
        self.body.setLayout(self.content_layout)
        self.controls = AppearanceControls(settings, self.body, theme_key, apply_callback)
        self.content_layout.addWidget(self.controls)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.body)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.scroll)
        layout.addWidget(self.buttons)
        self.fit_timer = QTimer(self)
        self.fit_timer.setSingleShot(True)
        self.fit_timer.timeout.connect(self.fit_to_screen)
        self.fit_timer.start(0)

    def fit_to_screen(self):
        screen = self.screen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1024, 768)
        cap_width, cap_height = max(1, area.width() - 32), max(1, area.height() - 64)
        self.body.layout().activate()
        self.layout().activate()
        margins = self.layout().contentsMargins()
        outer_w = margins.left() + margins.right()
        outer_h = margins.top() + margins.bottom() + self.layout().spacing()
        body_width = max(self.body.minimumSizeHint().width(), self.body.sizeHint().width())
        width = min(cap_width, body_width + outer_w)
        self.buttons.setOrientation(Qt.Horizontal)
        if self.buttons.minimumSizeHint().width() > width - outer_w:
            self.buttons.setOrientation(Qt.Vertical)
        needed = self.body.layout().totalHeightForWidth(max(1, width - outer_w))
        body_height = max(self.body.minimumSizeHint().height(), self.body.sizeHint().height(), needed)
        height = min(cap_height, body_height + outer_h + self.buttons.sizeHint().height())
        self.setFixedSize(width, height)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in {QEvent.FontChange, QEvent.ApplicationFontChange, QEvent.StyleChange}:
            if hasattr(self, "fit_timer"):
                self.fit_timer.start(0)

    def accept(self):
        from .appearance import configure_appearance
        self.controls.save_settings()
        configure_appearance(QApplication.instance(), self.settings, theme_key=self.theme_key)
        if self.apply_callback:
            self.apply_callback()
        super().accept()
