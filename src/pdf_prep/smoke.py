"""Explicit, offline package checks and retained synthetic output evidence.

This entry point is used by release validation, never by ordinary app startup.
All preferences and inputs are isolated, and retained artifacts contain only
documents created below. The report names artifacts relative to its directory.
"""
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import tempfile
import threading
import time

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from . import engine


def main(report: Path) -> int:
    report = report.absolute()
    report.parent.mkdir(parents=True, exist_ok=True)
    artifacts = report.parent / (report.stem + "-artifacts")
    artifacts.mkdir(exist_ok=True)
    checks = []
    evidence = []
    phase = "create synthetic inputs"
    window = None
    app = None
    working_path = None

    def retain(name, data):
        path = artifacts / name
        path.write_bytes(data)
        evidence.append({"path": path.relative_to(report.parent).as_posix(),
                         "sha256": hashlib.sha256(data).hexdigest(),
                         "size_bytes": len(data)})

    def retain_image(name, image):
        buffer = BytesIO()
        image.save(buffer, "PNG")
        retain(name, buffer.getvalue())

    try:
        with tempfile.TemporaryDirectory(prefix="pdf-prep-check-", dir=report.parent) as work:
            work = Path(work)
            working_path = work
            source = work / "synthetic.pdf"
            writer = PdfWriter()
            font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')}))
            for label in ('FIRST', 'SECOND'):
                page = writer.add_blank_page(width=420, height=595)
                page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
                stream = DecodedStreamObject()
                stream.set_data(f'BT /F1 28 Tf 35 500 Td ({label}) Tj ET'.encode())
                page[NameObject('/Contents')] = writer._add_object(stream)
            with source.open('wb') as stream:
                writer.write(stream)
            photo = work / 'synthetic.png'
            image = Image.new('RGBA', (600, 800), (0, 0, 0, 0))
            ImageDraw.Draw(image).rectangle((30, 30, 570, 770), fill=(25, 90, 160, 255))
            image.save(photo)
            image.close()
            scan = work / 'synthetic-scan.png'
            image = Image.effect_noise((1000, 1400), 90).convert('RGB')
            image.save(scan)
            image.close()
            originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, photo, scan)}

            phase = "assemble and render actual PDF bytes"
            pages = engine.inspect_file(source)
            result = engine.build_pdf([pages[1], pages[0], *engine.inspect_file(photo)])
            parsed = PdfReader(BytesIO(result.data))
            assert len(parsed.pages) == 3
            assert parsed.pages[0].extract_text().strip() == 'SECOND'
            assert parsed.pages[1].extract_text().strip() == 'FIRST'
            assert engine.render_pdf(result.data).width > 0
            checks.append('real assembly, page order, searchable text and rendered output')

            phase = "report impossible size target"
            target = engine.build_pdf(pages, mode='target', max_bytes=100)
            assert not target.target_met and len(target.data) > 100
            checks.append('impossible target reports failure')

            phase = "cancel at a page boundary"
            cancelled = threading.Event()
            progressed = []

            def cancel_after_page(current, total, message):
                progressed.append(current)
                cancelled.set()

            try:
                engine.build_pdf(pages, progress=cancel_after_page, cancelled=cancelled.is_set)
            except engine.CancelledError:
                pass
            else:
                raise AssertionError('cancelled build returned completed output')
            assert progressed == [1]
            checks.append('preparation cancellation stops at a page boundary')

            phase = "save without replacing a destination or source"
            destination = work / 'prepared.pdf'
            engine.save_new_pdf(result.data, destination, originals)
            before = destination.read_bytes()
            try:
                engine.save_new_pdf(result.data, destination, originals)
            except (FileExistsError, engine.DocumentError):
                pass
            else:
                raise AssertionError('existing destination was not protected')
            assert destination.read_bytes() == before
            try:
                engine.save_new_pdf(result.data, source, originals)
            except engine.DocumentError:
                pass
            else:
                raise AssertionError('source destination was not protected')
            assert originals == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
            checks.append('new file saved, existing destination refused, originals unchanged')

            from PySide6.QtCore import QItemSelectionModel, QSettings
            from PySide6.QtGui import QFontDatabase
            from PySide6.QtWidgets import QApplication
            from .app import MainWindow
            from .styles import FileDialogStyle
            app = QApplication.instance() or QApplication([])
            app.setStyle(FileDialogStyle())
            # Windows' offscreen plugin does not always enumerate system fonts.
            # Read the installed platform font explicitly for meaningful render
            # evidence; ordinary startup and settings are unaffected.
            font_paths = ([Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts/segoeui.ttf',
                           Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts/seguisb.ttf']
                          if os.name == 'nt' else
                          [Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')])
            for path in font_paths:
                if path.is_file():
                    QFontDatabase.addApplicationFont(str(path))
            if not QFontDatabase.families():
                raise RuntimeError('Install a readable system UI font before collecting rendered evidence')
            settings = QSettings(str(work / 'preferences.ini'), QSettings.Format.IniFormat)
            settings.setFallbacksEnabled(False)
            settings.setValue('appearance', 'light')
            window = MainWindow(settings=settings)
            window.show()

            def idle():
                end = time.monotonic() + 60
                settled = None
                while time.monotonic() < end:
                    app.processEvents()
                    # QThread Python work needs the GIL while this harness waits.
                    time.sleep(0.005)
                    if window.busy:
                        settled = None
                    elif settled is None:
                        settled = time.monotonic()
                    elif time.monotonic() - settled > 0.08:
                        return
                raise TimeoutError('Desktop worker did not finish')

            def select(rows):
                window.page_list.blockSignals(True)
                window.page_list.clearSelection()
                for row in rows:
                    window.page_list.item(row).setSelected(True)
                window.page_list.setCurrentRow(rows[0], QItemSelectionModel.SelectionFlag.NoUpdate)
                window.page_list.blockSignals(False)
                window._update_controls()

            def prepare():
                window.prepare_button.click()
                idle()
                assert window.result is not None
                return window.result

            def screenshot(name):
                app.processEvents()
                path = artifacts / name
                assert window.grab().save(str(path))
                data = path.read_bytes()
                evidence.append({"path": path.relative_to(report.parent).as_posix(),
                                 "sha256": hashlib.sha256(data).hexdigest(),
                                 "size_bytes": len(data)})

            phase = "desktop import, prepared preview and exact-byte saving"
            assert not window.save_button.isEnabled()
            window.load_paths([source, photo])
            idle()
            assert len(window.pages) == 3
            prepared = prepare().data
            assert window.save_button.isEnabled()
            assert '0 PDFs saved' in window.status_label.text()
            window.resize(1160, 820)
            window.set_theme('light')
            screenshot('desktop-light.png')
            window.set_theme('dark')
            screenshot('desktop-dark.png')
            window.resize(640, 600)
            app.processEvents()
            assert (window.width(), window.height()) == (640, 600)
            screenshot('narrow-dark.png')
            for button in (window.add_button, window.prepare_button, window.save_button, window.cancel_button):
                point = button.mapTo(window, button.rect().topLeft())
                assert point.x() >= 0 and point.y() >= 0
                assert point.x() + button.width() <= window.width()
                assert point.y() + button.height() <= window.height()
            window.resize(1160, 820)
            ui_output = work / 'desktop-saved.pdf'
            window.save_to(ui_output)
            idle()
            assert ui_output.read_bytes() == prepared
            assert 'Saved 1 PDF' in window.status_label.text()
            retain('prepared-output.pdf', ui_output.read_bytes())
            retain_image('prepared-page.png', engine.render_pdf(ui_output.read_bytes()))
            checks.append('desktop import, actual result preview and save identical bytes')
            checks.append('light and dark desktop screenshots; actions reachable at 640 x 600')

            phase = "cancel desktop import without changing prepared output"
            previous = window.result
            plan = list(window.pages)
            window.load_paths([scan])
            assert window.busy
            window.cancel_button.click()
            idle()
            assert window.pages == plan and window.result is previous
            assert window.save_button.isEnabled()
            assert 'Cancelled' in window.status_label.text()
            checks.append('desktop import cancellation preserves the existing plan and prepared bytes')

            phase = "desktop page editing and extraction"
            select([0])
            window.down_button.click()
            idle()
            assert [page.index for page in window.pages[:2]] == [1, 0]
            assert window.result is None and not window.save_button.isEnabled()
            select([0, 1])
            window.rotate_button.click()
            idle()
            assert [page.rotation for page in window.pages] == [90, 90, 0]
            select([0, 1])
            window.extract_button.click()
            idle()
            assert len(window.pages) == 2
            edited = prepare()
            parsed = PdfReader(BytesIO(edited.data))
            assert [page.extract_text().strip() for page in parsed.pages] == ['SECOND', 'FIRST']
            assert [page.rotation for page in parsed.pages] == [90, 90]
            retain('extracted-rotated-output.pdf', edited.data)
            retain_image('extracted-rotated-page.png', engine.render_pdf(edited.data))
            select([0])
            window.remove_button.click()
            idle()
            assert len(window.pages) == 1 and window.pages[0].index == 0
            assert window.result is None and not window.save_button.isEnabled()
            checks.append('desktop reorder, rotate, extract and remove invalidate stale output')

            phase = "desktop successful measured size fitting"
            window.clear_pages()
            window.load_paths([scan])
            idle()
            original_scan = prepare()
            limit_mb = max(0.10, math.floor(len(original_scan.data) * 0.60 / 10_000) / 100)
            window.mode_combo.setCurrentIndex(1)
            window.limit_spin.setValue(limit_mb)
            idle()
            fitted = prepare()
            assert fitted.rasterized and fitted.target_met
            assert len(fitted.data) <= round(limit_mb * 1_000_000)
            assert window.save_button.isEnabled()
            assert 'converted to images' in window.status_label.text()
            screenshot('size-fit-ready.png')
            fitted_path = work / 'size-fit.pdf'
            window.save_to(fitted_path)
            assert fitted_path.read_bytes() == fitted.data
            retain('size-fit-output.pdf', fitted_path.read_bytes())
            retain_image('size-fit-page.png', engine.render_pdf(fitted.data))
            assert originals == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}
            checks.append('desktop target fitting reduces actual bytes, discloses rasterization and saves exact output')

            window.set_theme('system')
            app.processEvents()
            window.close()
            window = None
            app.processEvents()
            checks.append('desktop appearance changes and safe close')
        receipt = {'passed': True, 'platform': platform.platform(),
                   'qt_platform': app.platformName(),
                   'atomic_save_method': 'windows-no-replace-rename' if os.name == 'nt' else 'posix-no-replace-hard-link',
                   'checks': checks, 'artifacts': evidence,
                   'synthetic_documents_only': True}
        report.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        return 0
    except Exception as exc:
        message = str(exc)
        if working_path is not None:
            message = message.replace(str(working_path), '[synthetic-workspace]')
        message = message.replace(str(report.parent), '[report-directory]')
        report.write_text(json.dumps({'passed': False, 'checks': checks,
                                     'failed_phase': phase, 'error_type': type(exc).__name__,
                                     'error': message, 'artifacts': evidence}, indent=2), encoding='utf-8')
        return 1
    finally:
        if window is not None:
            if window.worker is not None:
                window.worker.cancel_event.set()
                window.worker.wait(60_000)
                app.processEvents()
            window.close()
            app.processEvents()
