"""Local, preview-first PDF preparation desktop interface."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import os
import sys
import threading
from typing import Callable

from PIL import Image
from PySide6.QtCore import Qt, QItemSelectionModel, QSettings, QSize, QThread, Signal, QUrl, QTimer
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QImage, QKeySequence, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from . import __version__
from . import engine
from .styles import FileDialogStyle, refresh_file_dialog_icons
from .system_fonts import apply_system_font, system_font_css, accent_colors, accent_text, bind_appearance_refresh
from .desktop_geometry import available_geometry
from .flow_layout import FlowLayout
from .appearance import configure_appearance
from .appearance_controls import AppearanceSettingsDialog
from .content_dialog import TextContentDialog, fit_viewer


def image_pixmap(image: Image.Image) -> QPixmap:
    rgb = image.convert("RGB")
    data = rgb.tobytes()
    return QPixmap.fromImage(QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy())


def size_text(size: int) -> str:
    return f"{size / 1_000_000:.2f} MB ({size:,} bytes)"


def apply_theme(app: QApplication, choice: str) -> None:
    apply_system_font(app)
    colors = accent_colors(app)
    accent, on_accent = colors["accent"], colors["on_accent"]
    hover, pressed = colors["hover"], colors["pressed"]
    dark = app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    resolved_scheme = getattr(app, "_studio_appearance_values", {}).get("color_scheme")
    if choice == "system" and resolved_scheme in {"dark", "light"}:
        dark = resolved_scheme == "dark"
    if choice == "system" and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                dark = winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except OSError:
            pass
    if choice != "system":
        dark = choice == "dark"
    window, panel, field = ("#2c2c2c", "#252525", "#303030") if dark else ("#f3f3f3", "#ffffff", "#fafafa")
    text, muted, border = ("#f1f1f1", "#b5b5b5", "#555555") if dark else ("#222222", "#626262", "#cccccc")
    focus = "#f4f8ff" if dark else "#111111"
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, window), (QPalette.ColorRole.WindowText, text),
                        (QPalette.ColorRole.Base, field), (QPalette.ColorRole.Text, text),
                        (QPalette.ColorRole.Button, panel), (QPalette.ColorRole.ButtonText, text),
                        (QPalette.ColorRole.Mid, border),
                        (QPalette.ColorRole.Highlight, accent), (QPalette.ColorRole.HighlightedText, on_accent),
                        (QPalette.ColorRole.ToolTipBase, panel), (QPalette.ColorRole.ToolTipText, text)):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setProperty("dark", dark)
    apply_system_font(app)
    app.setStyleSheet(f"""
        QWidget {{ {system_font_css(app)} color: {text}; }}
        QMainWindow, QDialog {{ background: {window}; }}
        QPushButton, QToolButton {{ background: {panel}; border: 1px solid {border}; border-radius: 6px; padding: 6px 10px; min-height: 0px; }}
        QPushButton:hover, QToolButton:hover {{ background: {field}; border-color: {accent}; }}
        QPushButton:pressed, QToolButton:pressed {{ background: {pressed}; color: {on_accent}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {muted}; border-color: {border}; }}
        QPushButton:enabled:focus, QToolButton:enabled:focus, QComboBox:enabled:focus,
        QDoubleSpinBox:enabled:focus {{ border: 2px solid {focus}; }}
        QPushButton#savePdf {{ background: {accent}; border-color: {accent}; color: {on_accent}; font-weight: 600; min-width: 128px; }}
        QPushButton#savePdf:enabled:hover {{ background: {hover}; }}
        QPushButton#savePdf:enabled:focus {{ border-color: {on_accent}; }}
        QPushButton#savePdf:disabled {{ background: {border}; border-color: {border}; color: {muted}; }}
        QFrame#settingsPanel {{ background: transparent; border: 0; }}
        QLabel#muted {{ color: {muted}; }}
        QLabel#sectionTitle {{ font-weight: 600; }}
        QLabel#outputInfo {{ color: {accent_text(app, window)}; font-weight: 600; }}
        QLabel#status {{ font-weight: 600; }}
        QComboBox, QDoubleSpinBox {{ background: {field}; border: 1px solid {border}; border-radius: 6px; padding: 5px 7px; min-height: 0px; }}
        QComboBox QAbstractItemView, QMenu {{ background: {panel}; color: {text}; selection-background-color: {accent}; selection-color: {on_accent}; }}
        QMenu::item {{ padding: 8px 18px; }}
        QListWidget {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; selection-background-color: {accent}; selection-color: {on_accent}; outline: none; }}
        QListWidget::item {{ padding: 8px; border-bottom: 1px solid {border}; }}
        QListWidget::item:selected {{ background: {accent}; color: {on_accent}; }}
        QListWidget:focus {{ border: 2px solid {focus}; }}
        QProgressBar {{ background: {panel}; border: 1px solid {border}; border-radius: 5px; min-height: 12px; max-height: 12px; }}
        QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
        QSplitter::handle {{ background: {window}; }}
        QSplitter::handle:horizontal {{ width: 12px; }}
        QToolTip {{ background: {panel}; color: {text}; border: 1px solid {border}; padding: 6px; }}
        /* QFileDialog fixes these icon-only buttons to the style's icon width.
           Full text-button padding otherwise leaves no room to paint icons. */
        QFileDialog QToolButton {{ padding: 2px; min-height: 0px; }}
        QFileDialog QToolButton:enabled:focus {{ padding: 1px; }}
    """)
    app.setProperty("studioThemeChoice", choice)
    bind_appearance_refresh(app, lambda: apply_theme(app, app.property("studioThemeChoice") or "system"))
    refresh_file_dialog_icons(app)


class Preview(QWidget):
    def __init__(self):
        super().__init__()
        self.pixmap = QPixmap()
        self.empty_text = "Add PDFs or photos to begin"
        self.setMinimumSize(260, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Document page preview")

    def set_image(self, image: Image.Image | None) -> None:
        self.pixmap = image_pixmap(image) if image is not None else QPixmap()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        dark = bool(QApplication.instance().property("dark"))
        painter.fillRect(self.rect(), QColor("#222222" if dark else "#e3e5e8"))
        if self.pixmap.isNull():
            painter.setPen(QColor("#b5b5b5" if dark else "#626262"))
            painter.drawText(self.rect().adjusted(24, 24, -24, -24), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_text)
            return
        available = self.size() - QSize(32, 32)
        scaled = self.pixmap.size().scaled(available, Qt.AspectRatioMode.KeepAspectRatio)
        x, y = (self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(x + 3, y + 3, scaled.width(), scaled.height(), QColor("#111111" if dark else "#c1c4ca"))
        painter.drawPixmap(x, y, scaled.width(), scaled.height(), self.pixmap)


class Worker(QThread):
    progress = Signal(int, int, str)
    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, action: Callable):
        super().__init__()
        self.action = action
        self.cancel_event = threading.Event()

    def run(self):
        try:
            result = self.action(self.cancel_event.is_set, self.progress.emit)
            self.result_ready.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__)


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings("Steady State Studio", "PDF Prep")
        self.pages: list[engine.Page] = []
        self.thumbnails: list[Image.Image | None] = []
        self.result = None
        self.worker: Worker | None = None
        self.busy = False
        self._closing = False
        self._preview_image: Image.Image | None = None
        self._preview_key = None
        self.last_saved: Path | None = None
        self.theme = str(self.settings.value("appearance", "system"))
        if self.theme not in {"system", "light", "dark"}:
            self.theme = "system"
        configure_appearance(QApplication.instance(), self.settings, theme_key="appearance")
        apply_theme(QApplication.instance(), self.theme)
        self.setWindowTitle(f"PDF Prep {__version__} · Steady State Studio")
        self.setAcceptDrops(True)
        self.resize(1160, 820)
        self._build_ui()
        saved_geometry = self.settings.value("geometry")
        if saved_geometry is not None:
            self.restoreGeometry(saved_geometry)
        from .window_size import WindowSizeGuard
        self.window_size_guard = WindowSizeGuard(self, (780, 520), self.work_scroll)
        self._shortcuts()
        QApplication.instance().styleHints().colorSchemeChanged.connect(self._system_theme_changed)
        self._update_controls()

    def _button(self, label, name, slot, tooltip=""):
        button = QPushButton(label)
        button.setObjectName(name)
        button.setAccessibleName(label)
        button.setToolTip(tooltip)
        button.clicked.connect(slot)
        return button

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        toolbar = FlowLayout()
        self.toolbar_layout = toolbar
        self.add_button = self._button("Add Files", "addFiles", self.add_files, "Add PDFs, JPEGs and PNGs (Ctrl+O)")
        self.clear_button = self._button("Clear", "clearPages", self.clear_pages)
        toolbar.addWidget(self.add_button)
        toolbar.addWidget(self.clear_button)
        toolbar.addStretch()
        toolbar.addWidget(self._button("Help", "help", self.show_help))
        toolbar.addWidget(self._button("Donate", "donate", lambda: QDesktopServices.openUrl(QUrl("https://ko-fi.com/steadystatestudio"))))
        self.settings_button = self._button("Settings", "settings", self.show_settings)
        toolbar.addWidget(self.settings_button)
        root.addLayout(toolbar)

        work = QWidget()
        work_layout = QVBoxLayout(work)
        work_layout.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("settingsPanel")
        settings_grid = QGridLayout(panel)
        settings_grid.setContentsMargins(0, 0, 0, 0)
        settings_grid.setHorizontalSpacing(10)
        output_label = QLabel("Output")
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("outputMode")
        self.mode_combo.setAccessibleName("Output method")
        self.mode_combo.addItem("Preserve text and original quality", "original")
        self.mode_combo.addItem("Fit a maximum file size", "target")
        output_label.setBuddy(self.mode_combo)
        limit_label = QLabel("Maximum")
        self.limit_spin = QDoubleSpinBox()
        self.limit_spin.setObjectName("maxMegabytes")
        self.limit_spin.setAccessibleName("Maximum PDF file size in decimal megabytes")
        self.limit_spin.setRange(0.10, 500.00)
        self.limit_spin.setDecimals(2)
        self.limit_spin.setSingleStep(0.5)
        self.limit_spin.setSuffix(" MB")
        try:
            self.limit_spin.setValue(float(self.settings.value("max_mb", 4.0)))
        except (ValueError, TypeError):
            self.limit_spin.setValue(4.0)
        limit_label.setBuddy(self.limit_spin)
        settings_grid.addWidget(output_label, 0, 0)
        settings_grid.addWidget(self.mode_combo, 0, 1)
        settings_grid.addWidget(limit_label, 0, 2)
        settings_grid.addWidget(self.limit_spin, 0, 3)
        settings_grid.setColumnStretch(1, 1)
        self.mode_note = QLabel()
        self.mode_note.setObjectName("muted")
        self.mode_note.setWordWrap(True)
        settings_grid.addWidget(self.mode_note, 1, 0, 1, 4)
        work_layout.addWidget(panel)
        self.mode_combo.currentIndexChanged.connect(self.settings_changed)
        self.limit_spin.valueChanged.connect(self.settings_changed)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("workSplitter")
        splitter.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self.page_count = QLabel("Pages · 0")
        self.page_count.setObjectName("sectionTitle")
        left_layout.addWidget(self.page_count)
        self.page_list = QListWidget()
        self.page_list.setObjectName("pageList")
        self.page_list.setAccessibleName("Pages in output order; select pages to arrange or extract")
        self.page_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.page_list.setIconSize(QSize(72, 96))
        self.page_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.page_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_list.setMinimumWidth(220)
        self.page_list.setMinimumHeight(224)
        self.page_list.currentRowChanged.connect(self.selection_changed)
        self.page_list.itemSelectionChanged.connect(self._update_controls)
        left_layout.addWidget(self.page_list, 1)
        arrange = QGridLayout()
        arrange.setSpacing(6)
        self.up_button = self._button("Move up", "moveUp", lambda: self.move_pages(-1), "Move selected pages up (Alt+Up)")
        self.down_button = self._button("Move down", "moveDown", lambda: self.move_pages(1), "Move selected pages down (Alt+Down)")
        self.rotate_button = self._button("Rotate 90°", "rotatePages", self.rotate_pages, "Rotate selected pages clockwise (Ctrl+R)")
        self.remove_button = self._button("Remove", "removePages", self.remove_pages, "Remove selected pages from this plan (Delete)")
        self.extract_button = self._button("Extract selected", "extractPages", self.extract_selected, "Keep only selected pages in this output plan. Originals stay unchanged.")
        arrange.addWidget(self.up_button, 0, 0)
        arrange.addWidget(self.down_button, 0, 1)
        arrange.addWidget(self.rotate_button, 1, 0)
        arrange.addWidget(self.remove_button, 1, 1)
        arrange.addWidget(self.extract_button, 2, 0, 1, 2)
        left_layout.addLayout(arrange)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        preview_toolbar = QHBoxLayout()
        self.preview_mode = QComboBox()
        self.preview_mode.addItems(["Source page", "Prepared PDF"])
        self.preview_mode.setObjectName("previewMode")
        self.preview_mode.setAccessibleName("Preview source or prepared PDF")
        self.preview_mode.currentIndexChanged.connect(self.preview_changed)
        preview_toolbar.addWidget(self.preview_mode)
        preview_toolbar.addStretch()
        self.previous_button = self._button("‹", "previousPage", lambda: self.navigate(-1), "Previous page")
        self.previous_button.setAccessibleName("Previous page")
        self.next_button = self._button("›", "nextPage", lambda: self.navigate(1), "Next page")
        self.next_button.setAccessibleName("Next page")
        self.zoom_button = self._button("Zoom", "zoomPreview", self.show_zoom, "Inspect the current rendered page at full preview resolution")
        preview_toolbar.addWidget(self.previous_button)
        preview_toolbar.addWidget(self.next_button)
        preview_toolbar.addWidget(self.zoom_button)
        right_layout.addLayout(preview_toolbar)
        self.preview = Preview()
        self.preview.setObjectName("documentPreview")
        right_layout.addWidget(self.preview, 1)
        self.preview_caption = QLabel("PDF, JPEG and PNG · processed on your computer")
        self.preview_caption.setObjectName("muted")
        self.preview_caption.setWordWrap(True)
        right_layout.addWidget(self.preview_caption)
        self.output_info = QLabel("No PDF prepared yet. Originals remain unchanged.")
        self.output_info.setObjectName("outputInfo")
        self.output_info.setWordWrap(True)
        right_layout.addWidget(self.output_info)
        splitter.addWidget(right)
        splitter.setSizes([330, 780])
        self.work_splitter = splitter
        work_layout.addWidget(splitter, 1)
        self.work_scroll = QScrollArea()
        self.work_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.work_scroll.setWidgetResizable(True)
        self.work_scroll.setMinimumHeight(120)
        self.work_scroll.setWidget(work)
        root.addWidget(self.work_scroll, 1)

        actions = FlowLayout()
        self.actions_layout = actions
        self.prepare_button = self._button("Prepare preview", "preparePreview", self.prepare_output, "Build and inspect the actual output before saving (Ctrl+P)")
        self.save_button = self._button("Save PDF", "savePdf", self.save_dialog, "Save the inspected PDF as a new file (Ctrl+S)")
        self.cancel_button = self._button("Cancel", "cancelWork", self.cancel_work, "Stop safely; no output file is written")
        self.open_folder_button = self._button("Open folder", "openFolder", self.open_folder)
        actions.addWidget(self.prepare_button)
        actions.addStretch()
        actions.addWidget(self.save_button)
        actions.addStretch()
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.open_folder_button)
        root.addLayout(actions)
        self.status_label = QLabel("Add PDF files or photos, arrange the pages, then prepare a preview.")
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Operation status")
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setObjectName("operationProgress")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        progress_policy = self.progress.sizePolicy()
        progress_policy.setRetainSizeWhenHidden(True)
        self.progress.setSizePolicy(progress_policy)
        root.addWidget(self.progress)
        self.settings_changed()

    def _shortcuts(self):
        for sequence, callback in (("Ctrl+O", self.add_files), ("Ctrl+P", self.prepare_output), ("Ctrl+S", self.save_dialog),
                                   ("Delete", self.remove_pages), ("Alt+Up", lambda: self.move_pages(-1)),
                                   ("Alt+Down", lambda: self.move_pages(1)), ("Ctrl+R", self.rotate_pages)):
            action = QAction(self)
            action.setShortcut(QKeySequence(sequence))
            action.triggered.connect(callback)
            if sequence == "Delete":
                action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
                self.page_list.addAction(action)
            else:
                self.addAction(action)

    def set_theme(self, choice):
        self.theme = choice
        self.settings.setValue("appearance", choice)
        configure_appearance(QApplication.instance(), self.settings, theme_key="appearance")
        apply_theme(QApplication.instance(), choice)
        self.preview.update()

    def _reapply_appearance(self):
        self.theme = str(self.settings.value("appearance", "system"))
        configure_appearance(QApplication.instance(), self.settings, theme_key="appearance")
        apply_theme(QApplication.instance(), self.theme)
        self.preview.update()
        self._resize_page_rows()
        if hasattr(self, "window_size_guard"):
            self.window_size_guard.update()

    def show_settings(self):
        if not self.busy:
            dialog = AppearanceSettingsDialog(self.settings, self, theme_key="appearance",
                                              apply_callback=self._reapply_appearance)
            dialog.exec()

    def compact_minimum_width(self):
        def row_width(layout):
            items = [layout.itemAt(index) for index in range(layout.count())
                     if layout.itemAt(index).widget() and not layout.itemAt(index).isEmpty()]
            return sum(item.sizeHint().width() for item in items) + max(0, len(items)-1) * layout.spacing()
        return max(780, row_width(self.toolbar_layout) + 32, row_width(self.actions_layout) + 32,
                   self.work_splitter.minimumSizeHint().width() + 32)

    def _resize_page_rows(self):
        height = max(self.page_list.iconSize().height(), 2 * self.fontMetrics().lineSpacing()) + 16
        for row in range(self.page_list.count()):
            self.page_list.item(row).setSizeHint(QSize(210, height))

    def _system_theme_changed(self, *_):
        if self.theme == "system":
            apply_theme(QApplication.instance(), "system")
            self.preview.update()

    def settings_changed(self, *_):
        if self.busy:
            return
        target = self.mode_combo.currentData() == "target"
        self.limit_spin.setEnabled(target)
        self.mode_note.setText(
            "Size fitting may convert pages to images, losing searchable text and links. Inspect readability before saving. 1 MB = 1,000,000 bytes."
            if target else "Keeps PDF text and original page quality. Photos become PDF pages. Originals are never replaced."
        )
        self.settings.setValue("max_mb", self.limit_spin.value())
        self.invalidate_output()

    def selected_rows(self):
        return sorted(self.page_list.row(item) for item in self.page_list.selectedItems())

    def _update_controls(self):
        ready = bool(self.pages) and not self.busy
        selected = self.selected_rows()
        self.add_button.setEnabled(not self.busy)
        self.settings_button.setEnabled(not self.busy)
        self.clear_button.setEnabled(ready)
        self.page_list.setEnabled(not self.busy)
        self.mode_combo.setEnabled(not self.busy)
        self.limit_spin.setEnabled(not self.busy and self.mode_combo.currentData() == "target")
        self.prepare_button.setEnabled(ready)
        self.save_button.setEnabled(not self.busy and self.result is not None and self.result.target_met)
        self.cancel_button.setEnabled(self.busy and self.worker is not None and not self.worker.cancel_event.is_set())
        self.open_folder_button.setEnabled(not self.busy and self.last_saved is not None)
        self.up_button.setEnabled(ready and bool(selected) and selected[0] > 0)
        self.down_button.setEnabled(ready and bool(selected) and selected[-1] < len(self.pages) - 1)
        self.rotate_button.setEnabled(ready and bool(selected))
        self.remove_button.setEnabled(ready and bool(selected))
        self.extract_button.setEnabled(ready and bool(selected) and len(selected) < len(self.pages))
        index = self.page_list.currentRow()
        self.previous_button.setEnabled(ready and index > 0)
        self.next_button.setEnabled(ready and 0 <= index < len(self.pages) - 1)
        self.zoom_button.setEnabled(not self.busy and self._preview_image is not None)
        self.preview_mode.setEnabled(ready)
        self.preview_mode.model().item(1).setEnabled(self.result is not None)
        self.progress.setVisible(self.busy or self.last_saved is not None)

    def invalidate_output(self, message=None):
        self.result = None
        self._preview_key = None
        self.preview_mode.blockSignals(True)
        self.preview_mode.setCurrentIndex(0)
        self.preview_mode.blockSignals(False)
        self.output_info.setText("No PDF prepared yet. Originals remain unchanged.")
        self.last_saved = None
        index = self.page_list.currentRow()
        if 0 <= index < len(self.pages) and index < len(self.thumbnails):
            self._set_source_preview(index, self.thumbnails[index])
        if message is not None:
            self.status_label.setText(message)
        elif self.pages:
            self.status_label.setText(f"{len(self.pages)} pages in the plan · 0 PDFs saved. Prepare a preview to check the result.")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._update_controls()
        if self.pages and not self.busy:
            QTimer.singleShot(0, self.preview_changed)

    def _start_job(self, action, complete, status):
        if self.busy:
            return
        self.busy = True
        self.status_label.setText(status)
        self.progress.setRange(0, 0)
        self.worker = Worker(action)
        worker = self.worker
        worker.progress.connect(self._job_progress)
        worker.result_ready.connect(lambda value: self._job_result(worker, complete, value))
        worker.failed.connect(lambda text: self._job_failed(worker, text))
        worker.finished.connect(lambda: self._job_finished(worker))
        self._update_controls()
        worker.start()

    def _job_progress(self, current, total, message):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(current, total))
        else:
            self.progress.setRange(0, 0)
        self.status_label.setText(message)

    def _job_result(self, worker, complete, value):
        if worker.cancel_event.is_set():
            self.status_label.setText("Cancelled. No PDF was saved; the existing plan is unchanged.")
            if self.result is None:
                self.output_info.setText("No PDF prepared yet. Originals remain unchanged.")
            return
        complete(value)

    def _job_failed(self, worker, message):
        if worker.cancel_event.is_set():
            self.status_label.setText("Cancelled. No PDF was saved; the existing plan is unchanged.")
        else:
            self.status_label.setText("Could not complete the operation: " + message)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        if self.result is None:
            self.output_info.setText("No PDF prepared yet. Originals remain unchanged.")
        # A failed source preview must wait for a user retry, not schedule itself forever.
        if self._preview_key is None:
            self._preview_key = ("failed", self.page_list.currentRow())

    def _job_finished(self, worker):
        if self.worker is not worker:
            return
        self.busy = False
        self.worker = None
        worker.deleteLater()
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        self._update_controls()
        if self._closing:
            self.close()
        elif self.pages and self._preview_key is None:
            QTimer.singleShot(0, self.preview_changed)

    def cancel_work(self):
        if self.worker is not None:
            self.worker.cancel_event.set()
            self.status_label.setText("Cancelling at the next safe boundary… No output file will be written.")
            self._update_controls()

    def add_files(self):
        if self.busy:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Add PDFs and photos", "", "Documents and photos (*.pdf *.jpg *.jpeg *.png);;All files (*)")
        if paths:
            self.load_paths(paths)

    def load_paths(self, paths):
        if self.busy:
            return
        paths = [Path(path).expanduser().absolute() for path in paths]
        existing = {str(page.source.resolve()).casefold() if os.name == "nt" else str(page.source.resolve()) for page in self.pages}

        def action(cancelled, progress):
            added, thumbs, errors = [], [], []
            skipped = 0
            for file_index, path in enumerate(paths):
                if cancelled():
                    raise engine.CancelledError("Import cancelled")
                key = str(path.resolve()).casefold() if os.name == "nt" else str(path.resolve())
                if key in existing:
                    skipped += 1
                    continue
                try:
                    pages = engine.inspect_file(path)
                    if len(self.pages) + len(added) + len(pages) > engine.MAX_PAGES:
                        raise engine.DocumentError(f"would exceed the {engine.MAX_PAGES}-page output limit; extract fewer pages first")
                    file_thumbs = []
                    for page_number, page in enumerate(pages):
                        if cancelled():
                            raise engine.CancelledError("Import cancelled")
                        progress(page_number, len(pages), f"Reading {path.name} · page {page_number + 1} of {len(pages)}")
                        file_thumbs.append(engine.render_page(page, max_size=(144, 192)))
                    added.extend(pages)
                    thumbs.extend(file_thumbs)
                    existing.add(key)
                except engine.CancelledError:
                    raise
                except Exception as exc:
                    errors.append(f"{path.name}: {exc}")
                progress(file_index + 1, len(paths), f"Reading {file_index + 1} of {len(paths)} files · {len(added)} pages found")
            return added, thumbs, skipped, errors

        def complete(value):
            added, thumbs, skipped, errors = value
            old_count = len(self.pages)
            if added:
                self.pages.extend(added)
                self.thumbnails.extend(thumbs)
                self.rebuild_list([old_count])
                self.invalidate_output()
                self._set_source_preview(old_count, thumbs[0])
            details = f"Added {len(added)} pages · {skipped} already-listed files skipped · {len(errors)} files could not be added."
            self.status_label.setText(details + (" Prepare a preview before saving." if self.pages else " Add a supported PDF, JPEG or PNG."))
            if errors:
                message = QMessageBox(self)
                message.setWindowTitle("Some files could not be added")
                message.setIcon(QMessageBox.Icon.Warning)
                message.setText(details)
                message.setInformativeText("Usable files remain in your plan. No originals were changed.")
                message.setDetailedText("\n".join(errors))
                message.exec()

        self._start_job(action, complete, "Reading files locally…")

    def rebuild_list(self, selected=None):
        selected = selected if selected is not None else []
        self.page_list.blockSignals(True)
        self.page_list.clear()
        for index, page in enumerate(self.pages):
            item = QListWidgetItem(f"{index + 1} · {page.source.name}\nSource page {page.index + 1} · {page.rotation % 360}°")
            item.setSizeHint(QSize(210, max(self.page_list.iconSize().height(),
                                          2 * self.fontMetrics().lineSpacing()) + 16))
            item.setToolTip(f"Output page {index + 1}\n{page.source}\nSource page {page.index + 1}; rotation {page.rotation % 360}°")
            if self.thumbnails[index] is not None:
                item.setIcon(QIcon(image_pixmap(self.thumbnails[index])))
            self.page_list.addItem(item)
        for index in selected:
            if 0 <= index < len(self.pages):
                self.page_list.item(index).setSelected(True)
        if selected and self.pages:
            self.page_list.setCurrentRow(min(selected[0], len(self.pages) - 1), QItemSelectionModel.SelectionFlag.NoUpdate)
        elif self.pages:
            self.page_list.setCurrentRow(0)
        self.page_list.blockSignals(False)
        self.page_count.setText(f"Pages · {len(self.pages)}  /  output order")
        self._update_controls()

    def clear_pages(self):
        if self.busy:
            return
        self.pages.clear()
        self.thumbnails.clear()
        self.rebuild_list()
        self._preview_image = None
        self.preview.set_image(None)
        self.preview_caption.setText("PDF, JPEG and PNG · processed on your computer")
        self.invalidate_output("Plan cleared. Add PDF files or photos to begin.")

    def move_pages(self, direction):
        if self.busy:
            return
        selected = self.selected_rows()
        if not selected or (direction < 0 and selected[0] == 0) or (direction > 0 and selected[-1] == len(self.pages) - 1):
            return
        indices = selected if direction < 0 else reversed(selected)
        for index in indices:
            destination = index + direction
            self.pages[index], self.pages[destination] = self.pages[destination], self.pages[index]
            self.thumbnails[index], self.thumbnails[destination] = self.thumbnails[destination], self.thumbnails[index]
        self.invalidate_output()
        selected = [index + direction for index in selected]
        self.rebuild_list(selected)
        self._set_source_preview(selected[0], self.thumbnails[selected[0]])

    def rotate_pages(self):
        if self.busy:
            return
        selected = self.selected_rows()
        if not selected:
            return
        for index in selected:
            self.pages[index] = replace(self.pages[index], rotation=(self.pages[index].rotation + 90) % 360)
            if self.thumbnails[index] is not None:
                self.thumbnails[index] = self.thumbnails[index].transpose(Image.Transpose.ROTATE_270)
        self.invalidate_output()
        self.rebuild_list(selected)
        self._set_source_preview(selected[0], self.thumbnails[selected[0]])

    def remove_pages(self):
        if self.busy:
            return
        selected = self.selected_rows()
        if not selected:
            return
        for index in reversed(selected):
            self.pages.pop(index)
            self.thumbnails.pop(index)
        self.invalidate_output()
        new_index = min(selected[0], len(self.pages) - 1)
        self.rebuild_list([new_index] if self.pages else [])
        if self.pages:
            self._set_source_preview(new_index, self.thumbnails[new_index])
        else:
            self.preview.set_image(None)
            self._preview_image = None
            self.preview_caption.setText("Add PDFs or photos to begin")
        self._update_controls()

    def extract_selected(self):
        if self.busy:
            return
        selected = self.selected_rows()
        if not selected or len(selected) == len(self.pages):
            return
        self.pages = [self.pages[index] for index in selected]
        self.thumbnails = [self.thumbnails[index] for index in selected]
        self.invalidate_output(f"Extracted {len(self.pages)} selected pages into the output plan. Prepare a preview, then Save PDF. Originals are unchanged.")
        self.rebuild_list([0])
        self._set_source_preview(0, self.thumbnails[0])

    def _set_source_preview(self, index, image):
        if not 0 <= index < len(self.pages):
            return
        page = self.pages[index]
        self.preview.set_image(image)
        self._preview_image = image
        self._preview_key = None  # A thumbnail must be replaced before full-size inspection.
        self.preview_caption.setText(f"Page {index + 1} of {len(self.pages)} · {page.source.name} · source page {page.index + 1}")

    def selection_changed(self, *_):
        self._update_controls()
        self.preview_changed()

    def preview_changed(self, *_):
        if self.busy:
            return
        index = self.page_list.currentRow()
        if not 0 <= index < len(self.pages):
            return
        output = self.preview_mode.currentIndex() == 1 and self.result is not None
        key = (index, output, id(self.result) if output else self.pages[index])
        if key == self._preview_key:
            return
        page = self.pages[index]
        data = self.result.data if output else None

        def action(cancelled, progress):
            image = engine.render_pdf(data, index, max_size=(1500, 1800)) if output else engine.render_page(page, max_size=(1500, 1800))
            return image

        status_before = self.status_label.text()

        def complete(image):
            self._preview_image = image
            self.preview.set_image(image)
            self._preview_key = key
            kind = "Prepared PDF" if output else page.source.name + f" · source page {page.index + 1}"
            self.preview_caption.setText(f"Page {index + 1} of {len(self.pages)} · {kind}")
            self.status_label.setText(status_before)

        self._start_job(action, complete, "Rendering page preview…")

    def navigate(self, delta):
        if not self.busy:
            index = self.page_list.currentRow() + delta
            if 0 <= index < len(self.pages):
                self.page_list.setCurrentRow(index)

    def prepare_output(self):
        if self.busy or not self.pages:
            return
        pages = list(self.pages)
        mode = self.mode_combo.currentData()
        max_bytes = round(self.limit_spin.value() * 1_000_000) if mode == "target" else None
        self.result = None
        self.last_saved = None
        self.output_info.setText("Preparing the actual PDF bytes…")

        def action(cancelled, progress):
            result = engine.build_pdf(pages, mode=mode, max_bytes=max_bytes, progress=progress, cancelled=cancelled)
            if cancelled():
                raise engine.CancelledError("Preparation cancelled")
            image = engine.render_pdf(result.data, 0, max_size=(1500, 1800))
            return result, image

        def complete(value):
            self.result, image = value
            self.preview_mode.blockSignals(True)
            self.preview_mode.setCurrentIndex(1)
            self.preview_mode.blockSignals(False)
            self.page_list.blockSignals(True)
            self.page_list.setCurrentRow(0)
            self.page_list.blockSignals(False)
            self.preview.set_image(image)
            self._preview_image = image
            self._preview_key = (0, True, id(self.result))
            self.preview_caption.setText(f"Page 1 of {self.result.page_count} · Prepared PDF · actual generated output")
            fit = ""
            if max_bytes is not None:
                fit = f" · {'within' if self.result.target_met else 'exceeds'} {self.limit_spin.value():.2f} MB maximum"
            self.output_info.setText(f"Prepared: {self.result.page_count} pages · {size_text(len(self.result.data))}{fit}")
            if self.result.target_met:
                self.status_label.setText("Ready to save · 0 PDFs saved. " + self.result.description + " Inspect the prepared pages, then Save PDF.")
            else:
                self.status_label.setText("Size target not reached; saving is blocked. Increase the maximum or choose original quality, then prepare again. " + self.result.description)
            self.progress.setRange(0, 100)
            self.progress.setValue(100)

        self._start_job(action, complete, "Preparing PDF locally… No file has been saved.")

    def save_dialog(self):
        if self.busy or self.result is None or not self.result.target_met:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save a new PDF", "prepared-document.pdf", "PDF document (*.pdf)", options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            path = Path(path)
            if path.suffix.lower() != ".pdf":
                path = path.with_name(path.name + ".pdf")
            try:
                self.save_to(path)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "PDF not saved", str(exc) + "\n\nChoose a new filename. Originals and existing files are never replaced.")

    def save_to(self, path):
        """Save the inspected bytes. Exposed for exact package-output validation."""
        if self.busy or self.result is None or not self.result.target_met:
            raise ValueError("Prepare a valid output preview before saving")
        target = Path(path).absolute()
        try:
            engine.save_new_pdf(self.result.data, target, [page.source for page in self.pages])
        except Exception as exc:
            self.status_label.setText("PDF not saved: " + str(exc))
            raise
        self.last_saved = target
        self.status_label.setText(f"Saved 1 PDF · {self.result.page_count} pages · {size_text(len(self.result.data))}\n{target}")
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self._update_controls()
        return target

    def open_folder(self):
        if self.last_saved is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_saved.parent)))

    def show_zoom(self):
        if self.busy or self._preview_image is None:
            return
        # Source thumbnails are intentionally cheap; load the current page at inspection resolution.
        if self._preview_key is None:
            self.preview_changed()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Inspect page · " + self.preview_caption.text())
        layout = QVBoxLayout(dialog)
        label = QLabel()
        label.setPixmap(image_pixmap(self._preview_image))
        label.resize(label.pixmap().size())
        scroll = QScrollArea()
        scroll.setWidget(label)
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(scroll)
        note = QLabel("Rendered preview at up to 1,500 × 1,800 pixels. Inspect the saved PDF in your PDF viewer for further zoom.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        fit_viewer(dialog)
        dialog.exec()

    def show_help(self):
        text = (
            f"PDF Prep {__version__}\nA free, local tool from Steady State Studio.\n\n"
            "1. Add PDFs, JPEGs or PNGs. Drag files onto the window if you prefer.\n"
            "2. Select pages to move, rotate or remove. Ctrl/Shift selects multiple pages. Extract selected keeps only those pages in the plan.\n"
            "3. Choose original quality or a maximum size. Prepare preview builds the actual output and reports its size.\n"
            "4. Inspect the prepared PDF pages, then Save PDF to a new filename. Originals and existing files are never replaced.\n\n"
            "Size fitting tries original-quality output first. When needed, it converts pages to JPEG images and may remove searchable text and links. It never guarantees an arbitrary limit. Check small text, stamps and signatures visually.\n\n"
            "Encrypted PDFs, interactive forms and digitally signed PDFs are rejected; use a suitable PDF editor for those documents. This is not PDF/A validation, OCR or a redaction tool.\n\n"
            "Files are processed on your computer, without accounts, uploads or telemetry. Appearance and maximum-size preferences are saved locally; document paths are not retained. Donate opens the optional external Ko-fi page.\n\n"
            "Shortcuts: Ctrl+O add · Alt+Up/Down move · Ctrl+R rotate · Delete remove · Ctrl+P prepare · Ctrl+S save"
        )
        TextContentDialog("PDF Prep · Help", text, self, settings=self.settings,
                          width_key="dialogs/help/width").exec()

    def dragEnterEvent(self, event):
        if not self.busy and event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.busy:
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:
                self.load_paths(paths)
                event.acceptProposedAction()

    def closeEvent(self, event):
        if self.busy:
            if self._closing or QMessageBox.question(self, "Stop the current operation?", "Cancel the current operation and close when cleanup is safe? No output file will be written.") == QMessageBox.StandardButton.Yes:
                self._closing = True
                self.cancel_work()
            event.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.sync()
        self.window_size_guard.stop()
        event.accept()


def main() -> int:
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
    app = QApplication(sys.argv)
    from .system_fonts import capture_system_appearance
    capture_system_appearance(app)
    app.setApplicationName("PDF Prep")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Steady State Studio")
    app.setStyle(FileDialogStyle())
    from .appearance import prepare_initial_appearance, prepare_window_for_show
    settings = QSettings("Steady State Studio", "PDF Prep")
    prepare_initial_appearance(app, settings, theme_key="appearance")
    window = MainWindow(settings=settings)
    prepare_window_for_show(window)
    screen = window.screen() or app.primaryScreen()
    if screen is not None and not (window.isMaximized() or window.isFullScreen() or window.isMinimized()):
        area = available_geometry(window, screen.availableGeometry())
        window.resize(min(window.width(), area.width()), min(window.height(), area.height()))
    window.show()
    paths = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    if paths:
        QTimer.singleShot(0, lambda: window.load_paths(paths))
    return app.exec()
