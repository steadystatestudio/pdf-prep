"""Local document assembly, bounded rendering, and no-replace output writes.

The compression path deliberately rasterizes only after trying the original
document. Its returned description is part of the user-visible result contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import ctypes
import errno
import hashlib
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from typing import Callable, Iterable

from PIL import Image, ImageCms, ImageOps
from pypdf import PdfReader, PdfWriter
import pypdfium2 as pdfium


MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 512 * 1024 * 1024
MAX_PAGES = 1000
MAX_IMAGE_PIXELS = 40_000_000
MAX_RENDER_PIXELS = 16_000_000
_PDFIUM_LOCK = threading.RLock()


class DocumentError(ValueError):
    """An input or output cannot be handled safely."""


class CancelledError(Exception):
    """Work stopped at a page boundary; no destination was committed."""


@dataclass(frozen=True)
class Page:
    source: Path
    index: int = 0
    rotation: int = 0
    fingerprint: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "source", Path(self.source))
        if self.index < 0:
            raise DocumentError("Page numbers cannot be negative.")
        if self.rotation % 90:
            raise DocumentError("Page rotation must be a multiple of 90 degrees.")


@dataclass(frozen=True)
class BuildResult:
    data: bytes
    page_count: int
    target_met: bool
    description: str
    rasterized: bool


def _check_cancel(cancelled):
    if cancelled is not None and cancelled():
        raise CancelledError("Cancelled. No PDF was saved.")


def _progress(callback, current, total, message):
    if callback is not None:
        callback(current, total, message)


def _read_source(path: Path) -> bytes:
    if not path.is_file():
        raise DocumentError(f"{path.name}: file is missing or is not a regular file.")
    size = path.stat().st_size
    if size > MAX_SOURCE_BYTES:
        raise DocumentError(f"{path.name}: exceeds the 256 MB per-file limit.")
    if size == 0:
        raise DocumentError(f"{path.name}: file is empty.")
    with path.open("rb") as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise DocumentError(f"{path.name}: exceeds the 256 MB per-file limit.")
    return data


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _physical_page_size(page) -> tuple[float, float]:
    """Visible paper size in points, including crop, rotation and UserUnit.

    PDFium reports crop dimensions in default units but does not apply UserUnit.
    The exported image page must nevertheless retain its physical paper size.
    """
    media, crop = page.mediabox, page.cropbox
    unit = float(page.get("/UserUnit", 1))
    width = (min(float(media.right), float(crop.right)) -
             max(float(media.left), float(crop.left))) * unit
    height = (min(float(media.top), float(crop.top)) -
              max(float(media.bottom), float(crop.bottom))) * unit
    rotation = int(page.get("/Rotate", 0))
    if (rotation % 90 or not math.isfinite(unit) or unit <= 0 or
            not all(math.isfinite(v) and 0.01 <= v <= 20000 for v in (width, height))):
        raise DocumentError("PDF page dimensions, rotation or scaling are invalid or too large.")
    return (height, width) if rotation % 180 else (width, height)


def _reader(data: bytes, name: str) -> PdfReader:
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError(f"{name}: encrypted PDFs are unsupported. Export an unencrypted copy first.")
        count = len(reader.pages)
        if not count:
            raise DocumentError(f"{name}: PDF contains no pages.")
        if count > MAX_PAGES:
            raise DocumentError(f"{name}: exceeds the {MAX_PAGES}-page limit.")
        # Widgets require the complete AcroForm field hierarchy, and combining
        # signed pages invalidates their signature. Do not silently discard it.
        root = reader.trailer["/Root"]
        if root.get("/Perms"):
            raise DocumentError(f"{name}: certified or digitally signed PDFs are unsupported. Export an unsigned copy first.")
        form = root.get("/AcroForm")
        if form:
            form = form.get_object()
            if form.get("/Fields") or form.get("/XFA"):
                raise DocumentError(f"{name}: interactive forms or signatures are unsupported. Export a flattened, unsigned copy first.")
        for page in reader.pages:
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            if not all(math.isfinite(v) and 0 < v <= 20000 for v in (width, height)):
                raise DocumentError(f"{name}: page dimensions are invalid or too large.")
            _physical_page_size(page)
            annotations = page.get("/Annots", [])
            if hasattr(annotations, "get_object"):
                annotations = annotations.get_object()
            for annotation in annotations:
                annotation = annotation.get_object()
                if annotation.get("/Subtype") == "/Widget":
                    raise DocumentError(f"{name}: interactive forms or signatures are unsupported. Export a flattened, unsigned copy first.")
        return reader
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(f"{name}: PDF could not be read ({type(exc).__name__}).") from exc


def _open_image(data: bytes, name: str) -> Image.Image:
    try:
        with Image.open(BytesIO(data)) as original:
            if original.format not in ("JPEG", "PNG"):
                raise DocumentError(f"{name}: choose a PDF, JPEG, or PNG file.")
            if original.width * original.height > MAX_IMAGE_PIXELS:
                raise DocumentError(f"{name}: image exceeds the 40-megapixel limit.")
            if getattr(original, "n_frames", 1) != 1:
                raise DocumentError(f"{name}: animated images are unsupported. Export a still PNG first.")
            original.load()
            image = ImageOps.exif_transpose(original)
            icc = image.info.get("icc_profile")
            alpha = image.getchannel("A") if "A" in image.getbands() else None
            if icc:
                try:
                    profile = ImageCms.ImageCmsProfile(BytesIO(icc))
                    color_input = image if image.mode in ("RGB", "CMYK", "LAB", "L") else image.convert("RGB")
                    image = ImageCms.profileToProfile(color_input, profile,
                                                     ImageCms.createProfile("sRGB"), outputMode="RGB")
                except (ImageCms.PyCMSError, OSError, ValueError) as exc:
                    raise DocumentError(f"{name}: embedded colour profile cannot be converted to sRGB.") from exc
            if alpha is not None:
                canvas = Image.new("RGB", image.size, "white")
                canvas.paste(image.convert("RGB"), mask=alpha)
                return canvas
            # Palette transparency does not have an explicit alpha band.
            if "transparency" in image.info:
                rgba = image.convert("RGBA")
                canvas = Image.new("RGB", image.size, "white")
                canvas.paste(rgba, mask=rgba.getchannel("A"))
                return canvas
            return image.convert("RGB").copy()
    except DocumentError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise DocumentError(f"{name}: image could not be read.") from exc


def inspect_file(path: str | Path) -> list[Page]:
    """Validate a supported local file and return its pages in source order."""
    path = Path(path).resolve()
    try:
        data = _read_source(path)
        if path.suffix.lower() == ".pdf":
            count = len(_reader(data, path.name).pages)
        elif path.suffix.lower() in (".jpg", ".jpeg", ".png"):
            _open_image(data, path.name).close()
            count = 1
        else:
            raise DocumentError(f"{path.name}: choose a PDF, JPEG, or PNG file.")
        fingerprint = _digest(data)
        return [Page(path, index, fingerprint=fingerprint) for index in range(count)]
    except OSError as exc:
        raise DocumentError(f"{path.name}: file could not be opened ({exc.strerror or type(exc).__name__}).") from exc


def _source_data(page: Page) -> bytes:
    data = _read_source(page.source)
    if page.fingerprint is not None and _digest(data) != page.fingerprint:
        raise DocumentError(f"{page.source.name}: file changed after import. Remove it and add it again.")
    return data


def _render(data: bytes, index: int, max_size: tuple[int, int], rotation: int = 0) -> Image.Image:
    if len(max_size) != 2 or min(max_size) <= 0 or max_size[0] * max_size[1] > MAX_RENDER_PIXELS:
        raise DocumentError("Preview dimensions must be positive and at most 16 megapixels.")
    with _PDFIUM_LOCK:
        try:
            with pdfium.PdfDocument(data) as document:
                if not 0 <= index < len(document):
                    raise DocumentError("The selected page no longer exists.")
                page = document[index]
                try:
                    width, height = page.get_size()
                    target_w, target_h = max_size
                    if rotation % 180:
                        target_w, target_h = target_h, target_w
                    scale = min(target_w / width, target_h / height)
                    bitmap = page.render(scale=scale, rotation=rotation % 360,
                                         draw_annots=True, limit_image_cache=True)
                    try:
                        return bitmap.to_pil().convert("RGB").copy()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        except DocumentError:
            raise
        except Exception as exc:
            raise DocumentError(f"PDF page could not be rendered ({type(exc).__name__}).") from exc


def render_pdf(data: bytes, index: int = 0, max_size=(1200, 1500)) -> Image.Image:
    """Render the actual generated bytes, with bounded memory use."""
    return _render(data, index, max_size)


def render_page(page: Page, max_size=(1200, 1500)) -> Image.Image:
    if len(max_size) != 2 or min(max_size) <= 0 or max_size[0] * max_size[1] > MAX_RENDER_PIXELS:
        raise DocumentError("Preview dimensions must be positive and at most 16 megapixels.")
    data = _source_data(page)
    if page.source.suffix.lower() == ".pdf":
        return _render(data, page.index, max_size, page.rotation)
    if page.index != 0:
        raise DocumentError("Photos contain a single page.")
    image = _open_image(data, page.source.name)
    if page.rotation % 360:
        image = image.rotate(-page.rotation, expand=True)
    image.thumbnail(max_size, Image.Resampling.LANCZOS)
    return image


def _image_pdf(image: Image.Image, width_pt: float, height_pt: float, quality=92) -> bytes:
    # Pillow writes one JPEG-backed page. Explicit media box sizing below keeps
    # the physical paper size unchanged even when compression lowers resolution.
    stream = BytesIO()
    image.save(stream, format="PDF", resolution=72, quality=quality)
    reader = PdfReader(BytesIO(stream.getvalue()))
    writer = PdfWriter()
    page = writer.add_page(reader.pages[0])
    page.scale_to(width_pt, height_pt)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _assemble(pages, progress, cancelled) -> bytes:
    writer = PdfWriter()
    cache: dict[Path, tuple[bytes, PdfReader | None, str]] = {}
    total_source_bytes = 0
    for index, page in enumerate(pages):
        _check_cancel(cancelled)
        if page.source not in cache:
            data = _source_data(page)
            total_source_bytes += len(data)
            if total_source_bytes > MAX_TOTAL_SOURCE_BYTES:
                raise DocumentError("The combined source files exceed the 512 MB job limit. Prepare fewer files at a time.")
            reader = _reader(data, page.source.name) if page.source.suffix.lower() == ".pdf" else None
            cache[page.source] = data, reader, _digest(data)
        data, reader, fingerprint = cache[page.source]
        # Also detect inconsistent Page instances referring to different imports.
        if page.fingerprint is not None and fingerprint != page.fingerprint:
            raise DocumentError(f"{page.source.name}: file changed after import. Add it again.")
        if reader is not None:
            if page.index >= len(reader.pages):
                raise DocumentError(f"{page.source.name}: selected page does not exist.")
            # add_page clones into the writer; rotating this clone must never
            # mutate a cached source page used again by an extraction/duplicate.
            new_page = writer.add_page(reader.pages[page.index])
            if page.rotation % 360:
                new_page.rotate(page.rotation % 360)
        else:
            if page.index:
                raise DocumentError("Photos contain a single page.")
            image = _open_image(data, page.source.name)
            if page.rotation % 360:
                image = image.rotate(-page.rotation, expand=True)
            # A photo is fitted without cropping to at most an A4-long-edge
            # page, using 150 pixels per inch when it is smaller.
            scale = min(72 / 150, 841.89 / max(image.size))
            photo_pdf = _image_pdf(image, image.width * scale, image.height * scale)
            image.close()
            writer.add_page(PdfReader(BytesIO(photo_pdf)).pages[0])
        _progress(progress, index + 1, len(pages), f"Assembling page {index + 1} of {len(pages)}")
    _check_cancel(cancelled)
    # Do not copy source document metadata or executable document-level actions.
    writer.add_metadata({"/Producer": "PDF Prep - Steady State Studio"})
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _rasterize(data, dpi, quality, progress, cancelled) -> bytes:
    reader = PdfReader(BytesIO(data))
    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        _check_cancel(cancelled)
        width, height = _physical_page_size(page)
        scale = min(dpi / 72, math.sqrt(MAX_RENDER_PIXELS / (width * height)))
        size = max(1, int(width * scale)), max(1, int(height * scale))
        image = _render(data, index, size)
        image_bytes = _image_pdf(image, width, height, quality)
        image.close()
        writer.add_page(PdfReader(BytesIO(image_bytes)).pages[0])
        _progress(progress, index + 1, len(reader.pages), f"Checking {dpi} dpi: page {index + 1} of {len(reader.pages)}")
    _check_cancel(cancelled)
    writer.add_metadata({"/Producer": "PDF Prep - Steady State Studio"})
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def build_pdf(pages: Iterable[Page], *, max_bytes: int | None = None,
              mode: str = "original", progress: Callable | None = None,
              cancelled: Callable | None = None) -> BuildResult:
    """Build preview bytes; a target is a measured limit, never a size promise.

    Original mode retains PDF text/vector content. Target mode tries that first,
    then rasterizes at bounded readable presets. It does not perform OCR.
    """
    pages = list(pages)
    if not pages:
        raise DocumentError("Add at least one page before preparing a PDF.")
    if len(pages) > MAX_PAGES:
        raise DocumentError(f"A document can contain at most {MAX_PAGES} pages.")
    if mode not in ("original", "target"):
        raise DocumentError("Choose original quality or target size.")
    if max_bytes is not None and (isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0):
        raise DocumentError("The maximum file size must be a positive number of bytes.")
    if mode == "target" and max_bytes is None:
        raise DocumentError("Choose a maximum file size for target-size preparation.")
    _check_cancel(cancelled)
    data = _assemble(pages, progress, cancelled)
    if mode == "original" or len(data) <= max_bytes:
        return BuildResult(data, len(pages), max_bytes is None or len(data) <= max_bytes,
                           "Original PDF text and graphics retained; photos embedded as JPEGs.", False)
    best = data
    best_description = "Original quality retained; the size target could not be reached."
    rasterized = False
    for dpi, quality in ((150, 85), (150, 72), (120, 72), (96, 60), (96, 55)):
        _check_cancel(cancelled)
        candidate = _rasterize(data, dpi, quality, progress, cancelled)
        if len(candidate) < len(best):
            best = candidate
            rasterized = True
            best_description = (f"Pages converted to images at up to {dpi} dpi (JPEG quality {quality}). "
                                "Searchable text, links and annotations become pixels. Inspect every page for readability.")
        if len(best) <= max_bytes:
            return BuildResult(best, len(pages), True, best_description, rasterized)
    return BuildResult(best, len(pages), False,
                       "Size target could not be reached at the available quality settings. " + best_description,
                       rasterized)


_NO_REPLACE_UNSUPPORTED = {errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV, errno.EINVAL, errno.EACCES}


def _new_file_mode() -> int:
    """Ordinary permissions for a new document; mkstemp alone leaves it owner-only."""
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def _rename_no_replace(temporary, destination) -> bool:
    """Linux renameat2(RENAME_NOREPLACE). False where the filesystem lacks it."""
    if not sys.platform.startswith("linux"):
        return False
    rename = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if rename is None:
        return False
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(temporary), -100, os.fsencode(destination), 1) == 0:
        return True
    error = ctypes.get_errno()
    if error in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP):
        return False
    raise OSError(error, os.strerror(error), str(destination))


def _commit_no_replace(temporary, destination):
    """Publish the finished file without ever replacing an existing one.

    USB sticks, memory cards and many network folders (FAT, exFAT, SMB) have no
    hard links, so each platform tries the methods its filesystems support.
    """
    if os.name == "nt":
        # Windows rename is atomic and refuses an existing destination. Using
        # a rename also supports Windows folders where hard links are denied.
        os.rename(temporary, destination)
        return
    if _rename_no_replace(temporary, destination):
        return
    try:
        os.link(temporary, destination)
        return
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno not in _NO_REPLACE_UNSUPPORTED:
            raise
    # Exclusive creation still refuses an existing name. The copy is briefly
    # visible while it is written; an incomplete copy of ours is removed.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _new_file_mode())
    try:
        with os.fdopen(descriptor, "wb") as output, open(temporary, "rb") as staged:
            shutil.copyfileobj(staged, output)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        try:
            os.unlink(destination)
        except OSError:
            pass
        raise


def save_new_pdf(data: bytes, path: str | Path, source_paths: Iterable[str | Path] = ()) -> Path:
    """Commit complete bytes with an atomic no-replace operation.

    A temporary file is staged in the destination directory, then published
    with a no-replace rename, a hard link or exclusive creation, whichever the
    destination filesystem supports. An existing file is never replaced.
    """
    destination = Path(path).resolve()
    if destination.suffix.lower() != ".pdf":
        raise DocumentError("Choose an output filename ending in .pdf.")
    if any(destination == Path(source).resolve() for source in source_paths):
        raise DocumentError("Choose a new filename. Original files are never replaced.")
    if destination.exists():
        raise FileExistsError("That file already exists. Choose a new filename.")
    if not data.startswith(b"%PDF-"):
        raise DocumentError("Prepared output is not a PDF.")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".pdf-prep-", suffix=".tmp", dir=destination.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            if os.name != "nt":
                try:
                    os.fchmod(stream.fileno(), _new_file_mode())
                except OSError:
                    pass  # The destination has no permission bits to set.
            os.fsync(stream.fileno())
        _commit_no_replace(temporary, destination)
    except FileExistsError:
        raise FileExistsError("That file already exists. Choose a new filename.") from None
    except OSError as exc:
        raise DocumentError(f"PDF could not be saved: {exc.strerror or type(exc).__name__}. Choose a local folder with space and permission to write new files.") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return destination
