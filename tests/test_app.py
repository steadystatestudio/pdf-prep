"""Exercise the actual Qt workflow, generated output, and keyboard safeguards."""
from io import BytesIO
from pathlib import Path
import threading
import time

from PIL import Image
from pypdf import PdfReader, PdfWriter
import pytest
from PySide6.QtCore import QItemSelectionModel, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QToolButton

from pdf_prep import engine
from pdf_prep.app import MainWindow, apply_theme
from pdf_prep.styles import FileDialogStyle


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setStyle(FileDialogStyle())
    return instance


def _rendered_icon_images(app, button):
    icon = button.icon()
    painted = button.grab().toImage()
    button.setIcon(QIcon())
    app.processEvents()
    empty = button.grab().toImage()
    button.setIcon(icon)
    app.processEvents()
    return painted, empty


def _readable_icon_pixels(app, button):
    painted, empty = _rendered_icon_images(app, button)

    def luminance(color):
        channels = [value / 255 for value in (color.red(), color.green(), color.blue())]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                  for value in channels]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    count = 0
    for y in range(min(painted.height(), empty.height())):
        for x in range(min(painted.width(), empty.width())):
            ink, background = painted.pixelColor(x, y), empty.pixelColor(x, y)
            if ink != background:
                light, dark = sorted((luminance(ink), luminance(background)), reverse=True)
                count += (light + 0.05) / (dark + 0.05) >= 4.5
    return count


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("focused", [False, True])
def test_file_dialog_navigation_icons_are_painted(app, tmp_path, theme, focused):
    previous_style = app.styleSheet()
    previous_palette = app.palette()
    previous_font = app.font()
    previous_dark = app.property("dark")
    dialog = None
    try:
        apply_theme(app, theme)
        dialog = QFileDialog()
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
        dialog.setDirectory(str(tmp_path))
        dialog.resize(760, 520)
        dialog.show()
        app.processEvents()
        # Check initial appearance and an actual transition of this same dialog.
        for current in (theme, "dark" if theme == "light" else "light"):
            apply_theme(app, current)
            app.processEvents()
            for name in ("backButton", "forwardButton", "toParentButton", "newFolderButton",
                         "listModeButton", "detailModeButton"):
                button = dialog.findChild(QToolButton, name)
                assert button is not None
                assert not button.icon().isNull()
                assert not button.icon().pixmap(button.iconSize()).isNull()
                if focused and button.isEnabled():
                    button.setFocus()
                    app.processEvents()
                painted, empty = _rendered_icon_images(app, button)
                changed = sum(painted.pixelColor(x, y) != empty.pixelColor(x, y)
                              for y in range(min(painted.height(), empty.height()))
                              for x in range(min(painted.width(), empty.width())))
                assert changed > 16, f"{name}: icon exists but is not visibly painted ({changed} pixels)"
                if name in ("backButton", "forwardButton", "toParentButton"):
                    was_enabled = button.isEnabled()
                    button.setEnabled(True)
                    if focused:
                        button.setFocus()
                    app.processEvents()
                    count = _readable_icon_pixels(app, button)
                    assert count >= 16, f"{name}: enabled arrow lacks readable contrast ({count} pixels)"
                    button.setEnabled(was_enabled)
    finally:
        if dialog is not None:
            dialog.close()
            app.processEvents()
        app.setStyleSheet(previous_style)
        app.setPalette(previous_palette)
        app.setFont(previous_font)
        app.setProperty("dark", previous_dark)


def wait_idle(app, window, timeout=20):
    deadline = time.monotonic() + timeout
    settled = None
    while time.monotonic() < deadline:
        app.processEvents()
        # Release the GIL for Python work in QThread; qWait alone starves it.
        time.sleep(0.01)
        if window.busy:
            settled = None
        elif settled is None:
            settled = time.monotonic()
        elif time.monotonic() - settled > 0.08:
            return
    raise AssertionError("Operation timed out: " + window.status_label.text())


@pytest.fixture
def window(app, tmp_path):
    settings = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    settings.setValue("appearance", "light")
    widget = MainWindow(settings)
    widget.show()
    app.processEvents()
    yield widget
    if widget.busy:
        widget.cancel_work()
        wait_idle(app, widget)
    widget.close()
    app.processEvents()


@pytest.fixture
def sources(tmp_path):
    pdf = tmp_path / "document.pdf"
    writer = PdfWriter()
    for width, height in ((200, 300), (240, 320), (300, 400)):
        writer.add_blank_page(width=width, height=height)
    with pdf.open("wb") as stream:
        writer.write(stream)
    photo = tmp_path / "photo.png"
    Image.new("RGB", (180, 240), "#195f8c").save(photo)
    return pdf, photo


def select(window, rows):
    window.page_list.blockSignals(True)
    window.page_list.clearSelection()
    for row in rows:
        window.page_list.item(row).setSelected(True)
    window.page_list.setCurrentRow(rows[0], QItemSelectionModel.SelectionFlag.NoUpdate)
    window.page_list.blockSignals(False)
    window._update_controls()


def load(app, window, sources):
    window.load_paths(sources)
    wait_idle(app, window)
    assert len(window.pages) == 4


def prepare(app, window):
    window.prepare_output()
    wait_idle(app, window)
    assert window.result is not None
    return window.result


def test_multi_page_arrangement_extraction_and_remove_invalidate_output(app, window, sources):
    load(app, window, sources)
    prepare(app, window)
    select(window, [0, 2])
    window.move_pages(1)
    wait_idle(app, window)
    assert [(p.source.name, p.index) for p in window.pages] == [
        ("document.pdf", 1), ("document.pdf", 0), ("photo.png", 0), ("document.pdf", 2)]
    assert window.result is None and not window.save_button.isEnabled()
    prepare(app, window)
    select(window, [1, 3])
    window.rotate_pages()
    wait_idle(app, window)
    assert [p.rotation for p in window.pages] == [0, 90, 0, 90]
    assert window.result is None
    prepare(app, window)
    select(window, [1, 3])
    window.extract_selected()
    wait_idle(app, window)
    assert [p.index for p in window.pages] == [0, 2]
    assert all(p.rotation == 90 for p in window.pages)
    assert window.result is None
    result = prepare(app, window)
    assert result.page_count == 2
    assert [p.rotation for p in PdfReader(BytesIO(result.data)).pages] == [90, 90]
    select(window, [0])
    window.remove_pages()
    wait_idle(app, window)
    assert len(window.pages) == 1 and window.pages[0].index == 2
    assert window.result is None and not window.save_button.isEnabled()


def test_saved_file_is_exact_inspected_bytes_and_originals_are_untouched(app, window, sources, tmp_path):
    originals = {path: path.read_bytes() for path in sources}
    load(app, window, sources)
    result = prepare(app, window)
    assert "0 PDFs saved" in window.status_label.text()
    destination = tmp_path / "prepared.pdf"
    window.save_to(destination)
    assert destination.read_bytes() == result.data
    assert len(PdfReader(destination).pages) == 4
    assert "Saved 1 PDF" in window.status_label.text()
    assert str(destination) in window.status_label.text()
    assert window.open_folder_button.isEnabled()
    for path, content in originals.items():
        assert path.read_bytes() == content
    with pytest.raises((ValueError, OSError)):
        window.save_to(destination)
    assert destination.read_bytes() == result.data


def test_target_failure_displays_actual_size_and_blocks_save(app, window, tmp_path):
    paths = [tmp_path / "scan-one.png", tmp_path / "scan-two.png"]
    for path in paths:
        Image.effect_noise((1000, 1400), 100).convert("RGB").save(path)
    window.load_paths(paths)
    wait_idle(app, window)
    window.mode_combo.setCurrentIndex(1)
    window.limit_spin.setValue(0.10)
    wait_idle(app, window)
    result = prepare(app, window)
    assert not result.target_met and len(result.data) > 100_000
    assert "exceeds 0.10 MB" in window.output_info.text()
    assert "Size target not reached" in window.status_label.text()
    assert not window.save_button.isEnabled()
    with pytest.raises(ValueError, match="valid output preview"):
        window.save_to(tmp_path / "must-not-exist.pdf")
    assert not (tmp_path / "must-not-exist.pdf").exists()


def test_cancelling_import_retains_existing_plan_and_prepared_bytes(app, window, sources, tmp_path, monkeypatch):
    load(app, window, sources)
    previous = prepare(app, window)
    before = list(window.pages)
    started = threading.Event()
    release = threading.Event()
    real_inspect = engine.inspect_file
    new_photo = tmp_path / "new-photo.png"
    Image.new("RGB", (100, 140), "white").save(new_photo)

    def controlled_inspect(path):
        started.set()
        assert release.wait(5)
        return real_inspect(path)

    monkeypatch.setattr(engine, "inspect_file", controlled_inspect)
    window.load_paths([new_photo])
    assert started.wait(5)
    assert window.busy
    window.cancel_button.click()
    release.set()
    wait_idle(app, window)
    assert window.pages == before
    assert window.result is previous
    assert window.save_button.isEnabled()
    assert "Cancelled" in window.status_label.text()


def test_delete_shortcut_cannot_remove_pages_from_size_field_and_narrow_actions_fit(app, window, sources):
    load(app, window, sources)
    select(window, [0])
    window.mode_combo.setCurrentIndex(1)
    wait_idle(app, window)
    window.limit_spin.setFocus()
    app.processEvents()
    QTest.keyClick(window.limit_spin, Qt.Key.Key_Delete)
    wait_idle(app, window)
    assert len(window.pages) == 4
    window.page_list.setFocus()
    app.processEvents()
    QTest.keyClick(window.page_list, Qt.Key.Key_Delete)
    wait_idle(app, window)
    assert len(window.pages) == 3
    QTest.keyClick(window.page_list, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier)
    wait_idle(app, window)
    assert window.result is not None
    window.resize(640, 600)
    app.processEvents()
    for button in (window.add_button, window.prepare_button, window.save_button, window.cancel_button):
        top_left = button.mapTo(window, button.rect().topLeft())
        assert top_left.x() >= 0 and top_left.y() >= 0
        assert top_left.x() + button.width() <= window.width()
        assert top_left.y() + button.height() <= window.height()


@pytest.mark.parametrize("points", [11, 16, 20])
def test_system_font_is_preserved_with_reachable_compact_actions(app, tmp_path, points, monkeypatch):
    from PySide6.QtGui import QFont
    original = QFont(app.font())
    font = QFont(original)
    font.setPointSizeF(points)
    app.setFont(font)
    try:
        window = MainWindow(QSettings(str(tmp_path / "font.ini"), QSettings.Format.IniFormat))
        _use_test_screen(window, monkeypatch, 800, 600)
        window.show()
        window.resize(800, 600)
        QTest.qWait(100)
        for choice in ("light", "dark", "system"):
            window.set_theme(choice)
            app.processEvents()
            assert app.font() == font
        assert window.width() <= 800 and window.height() <= 600
        for button in (window.add_button, window.prepare_button, window.save_button, window.cancel_button):
            assert button.font().pointSizeF() == points
            assert button.height() >= button.fontMetrics().height()
            assert window.rect().contains(button.mapTo(window, button.rect().bottomRight()))
        window.work_scroll.ensureWidgetVisible(window.mode_combo)
        app.processEvents()
        view = window.work_scroll.viewport()
        assert view.rect().contains(window.mode_combo.mapTo(view, window.mode_combo.rect().center()))
        window.close()
    finally:
        app.setFont(original)
        apply_theme(app, "system")


def _use_test_screen(window, monkeypatch, width, height):
    """Model a monitor work area, rather than requesting an undersized window."""
    from types import SimpleNamespace
    from PySide6.QtCore import QRect
    screen = window.screen()
    replacement = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, width, height),
                                  availableGeometryChanged=screen.availableGeometryChanged)
    monkeypatch.setattr(window, "screen", lambda: replacement)
    window.window_size_guard.update()
