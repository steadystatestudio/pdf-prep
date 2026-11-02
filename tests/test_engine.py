from dataclasses import replace
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           NameObject, NumberObject, RectangleObject, TextStringObject)
import pytest

from pdf_prep import engine


def make_pdf(path, labels=("FIRST", "SECOND")):
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    font_ref = writer._add_object(font)
    for label in labels:
        page = writer.add_blank_page(width=300, height=450)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 28 Tf 20 360 Td ({label}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with Path(path).open("wb") as stream:
        writer.write(stream)
    return path


def text(data):
    return [page.extract_text().strip() for page in PdfReader(BytesIO(data)).pages]


def test_assembly_preserves_real_page_order_rotation_and_originals(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    original = source.read_bytes()
    pages = engine.inspect_file(source)
    result = engine.build_pdf([replace(pages[1], rotation=90), pages[0]])
    assert text(result.data) == ["SECOND", "FIRST"]
    parsed = PdfReader(BytesIO(result.data))
    assert parsed.pages[0].rotation == 90
    assert parsed.pages[1].rotation == 0
    assert result.page_count == 2
    assert result.target_met and not result.rasterized
    assert source.read_bytes() == original
    preview = engine.render_pdf(result.data, max_size=(450, 450))
    assert preview.width > preview.height


def test_duplicate_source_page_rotation_does_not_modify_other_copy(tmp_path):
    source = make_pdf(tmp_path / "source.pdf", ("ONE",))
    page = engine.inspect_file(source)[0]
    result = engine.build_pdf([replace(page, rotation=90), page, replace(page, rotation=180)])
    assert [p.rotation for p in PdfReader(BytesIO(result.data)).pages] == [90, 0, 180]


def test_extraction_is_just_selected_pages_and_keeps_text(tmp_path):
    source = make_pdf(tmp_path / "source.pdf", ("ONE", "TWO", "THREE"))
    pages = engine.inspect_file(source)
    assert text(engine.build_pdf([pages[2]]).data) == ["THREE"]


def test_png_transparency_and_orientation_match_export(tmp_path):
    source = tmp_path / "transparent.png"
    image = Image.new("RGBA", (240, 120), (255, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((0, 0, 100, 119), fill=(0, 50, 220, 255))
    image.save(source)
    page = replace(engine.inspect_file(source)[0], rotation=90)
    preview = engine.render_page(page)
    assert preview.size == (120, 240)
    assert preview.getpixel((60, 200)) == (255, 255, 255)
    result = engine.build_pdf([page])
    actual = engine.render_pdf(result.data, max_size=(240, 240))
    assert actual.height > actual.width
    assert all(v > 245 for v in actual.getpixel((actual.width // 2, actual.height - 5)))


def test_exif_orientation_honoured(tmp_path):
    source = tmp_path / "camera.jpg"
    image = Image.new("RGB", (80, 40), "blue")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    preview = engine.render_page(engine.inspect_file(source)[0])
    assert preview.size == (40, 80)


def test_target_preserves_text_when_original_already_fits(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    result = engine.build_pdf(engine.inspect_file(source), mode="target", max_bytes=1_000_000)
    assert result.target_met and not result.rasterized
    assert text(result.data) == ["FIRST", "SECOND"]


def test_target_reduction_measured_and_rasterization_disclosed(tmp_path):
    source = tmp_path / "noise.png"
    image = Image.effect_noise((1000, 1400), 90).convert("RGB")
    image.save(source)
    pages = engine.inspect_file(source)
    original = engine.build_pdf(pages)
    result = engine.build_pdf(pages, mode="target", max_bytes=int(len(original.data) * 0.6))
    assert len(result.data) < len(original.data)
    assert result.target_met == (len(result.data) <= int(len(original.data) * 0.6))
    assert result.rasterized
    assert "converted to images" in result.description
    assert "readability" in result.description
    assert len(PdfReader(BytesIO(result.data)).pages) == 1
    assert engine.render_pdf(result.data).width > 0


def test_impossible_target_reports_failure_and_returns_real_preview(tmp_path):
    source = make_pdf(tmp_path / "source.pdf", ("ONE",))
    result = engine.build_pdf(engine.inspect_file(source), mode="target", max_bytes=1)
    assert not result.target_met
    assert len(result.data) > 1
    assert "could not be reached" in result.description
    assert engine.render_pdf(result.data).height > 0


def test_pdf_and_image_combined(tmp_path):
    source = make_pdf(tmp_path / "source.pdf", ("FIRST",))
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (120, 240), "red").save(photo)
    pages = engine.inspect_file(source) + engine.inspect_file(photo)
    result = engine.build_pdf(pages)
    assert result.page_count == 2
    assert text(result.data)[0] == "FIRST"
    preview = engine.render_pdf(result.data, 1, (100, 200))
    pixel = preview.getpixel((preview.width // 2, preview.height // 2))
    assert pixel[0] > 240 and pixel[1] < 20


def test_source_changed_after_import_fails(tmp_path):
    source = make_pdf(tmp_path / "source.pdf", ("OLD",))
    pages = engine.inspect_file(source)
    make_pdf(source, ("NEW",))
    with pytest.raises(engine.DocumentError, match="changed after import"):
        engine.build_pdf(pages)
    with pytest.raises(engine.DocumentError, match="changed after import"):
        engine.render_page(pages[0])


def test_malformed_unsupported_empty_and_missing_inputs(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a PDF")
    with pytest.raises(engine.DocumentError, match="could not be read"):
        engine.inspect_file(bad)
    bad.write_bytes(b"")
    with pytest.raises(engine.DocumentError, match="empty"):
        engine.inspect_file(bad)
    bad = tmp_path / "file.txt"
    bad.write_text("hello")
    with pytest.raises(engine.DocumentError, match="PDF, JPEG, or PNG"):
        engine.inspect_file(bad)
    with pytest.raises(engine.DocumentError, match="missing"):
        engine.inspect_file(tmp_path / "absent.pdf")


def test_encrypted_pdf_rejected_even_with_empty_password(tmp_path):
    source = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("")
    writer.write(source)
    with pytest.raises(engine.DocumentError, match="encrypted PDFs"):
        engine.inspect_file(source)


def test_interactive_form_not_silently_broken(tmp_path):
    source = tmp_path / "form.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    field = DictionaryObject({NameObject("/FT"): NameObject("/Tx"),
                              NameObject("/T"): TextStringObject("name")})
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject({
        NameObject("/Fields"): ArrayObject([writer._add_object(field)])})
    writer.write(source)
    with pytest.raises(engine.DocumentError, match="interactive forms or signatures"):
        engine.inspect_file(source)


def test_no_clobber_source_or_existing_file_and_no_temp_left(tmp_path):
    source = make_pdf(tmp_path / "source.pdf", ("ONE",))
    original = source.read_bytes()
    result = engine.build_pdf(engine.inspect_file(source))
    with pytest.raises(engine.DocumentError, match="Original files"):
        engine.save_new_pdf(result.data, source, [source])
    unrelated = tmp_path / "exists.pdf"
    unrelated.write_bytes(b"keep me")
    with pytest.raises(FileExistsError):
        engine.save_new_pdf(result.data, unrelated, [source])
    output = tmp_path / "prepared.pdf"
    assert engine.save_new_pdf(result.data, output, [source]) == output
    assert output.read_bytes() == result.data
    assert source.read_bytes() == original
    assert unrelated.read_bytes() == b"keep me"
    assert not list(tmp_path.glob(".pdf-prep-*"))


def test_save_race_does_not_replace_destination(tmp_path, monkeypatch):
    source = make_pdf(tmp_path / "source.pdf", ("ONE",))
    result = engine.build_pdf(engine.inspect_file(source))
    output = tmp_path / "race.pdf"
    real_commit = engine._commit_no_replace
    def race_link(src, dst):
        output.write_bytes(b"someone else's file")
        real_commit(src, dst)
    monkeypatch.setattr(engine, "_commit_no_replace", race_link)
    with pytest.raises(FileExistsError):
        engine.save_new_pdf(result.data, output)
    assert output.read_bytes() == b"someone else's file"
    assert not list(tmp_path.glob(".pdf-prep-*"))


def test_save_failure_cleans_staged_file(tmp_path, monkeypatch):
    source = make_pdf(tmp_path / "source.pdf", ("ONE",))
    result = engine.build_pdf(engine.inspect_file(source))
    def fail(*args):
        raise OSError("unsupported links")
    monkeypatch.setattr(engine, "_commit_no_replace", fail)
    with pytest.raises(engine.DocumentError, match="could not be saved"):
        engine.save_new_pdf(result.data, tmp_path / "new.pdf")
    assert not list(tmp_path.glob(".pdf-prep-*"))
    assert not (tmp_path / "new.pdf").exists()


def test_cancel_at_start_and_page_boundary(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    pages = engine.inspect_file(source)
    with pytest.raises(engine.CancelledError):
        engine.build_pdf(pages, cancelled=lambda: True)
    seen = []
    def progress(current, total, message):
        seen.append((current, total, message))
    with pytest.raises(engine.CancelledError):
        engine.build_pdf(pages, progress=progress, cancelled=lambda: len(seen) > 0)
    assert seen[0][0:2] == (1, 2)
    assert not list(tmp_path.glob(".pdf-prep-*"))


@pytest.mark.parametrize("kwargs", [dict(mode="wrong"), dict(mode="target"),
                                   dict(max_bytes=0), dict(max_bytes=-1), dict(max_bytes=True)])
def test_invalid_settings(kwargs, tmp_path):
    source = make_pdf(tmp_path / "source.pdf", ("ONE",))
    with pytest.raises(engine.DocumentError):
        engine.build_pdf(engine.inspect_file(source), **kwargs)


def test_empty_document_blocked():
    with pytest.raises(engine.DocumentError, match="at least one page"):
        engine.build_pdf([])


def test_limits_reject_before_decoding_large_files(tmp_path, monkeypatch):
    source = tmp_path / "large.png"
    Image.new("RGB", (50, 50), "white").save(source)
    monkeypatch.setattr(engine, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(engine.DocumentError, match="40-megapixel"):
        engine.inspect_file(source)
    monkeypatch.setattr(engine, "MAX_SOURCE_BYTES", 1)
    with pytest.raises(engine.DocumentError, match="256 MB"):
        engine.inspect_file(source)


def test_source_metadata_is_not_republished(tmp_path):
    source = make_pdf(tmp_path / "source.pdf", ("ONE",))
    reader = PdfReader(source)
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    writer.add_metadata({"/Author": "Synthetic Private Person", "/Title": "Private filename"})
    writer.write(source)
    result = engine.build_pdf(engine.inspect_file(source))
    assert PdfReader(BytesIO(result.data)).metadata.get("/Author") is None
    assert b"Synthetic Private Person" not in result.data


def test_cropped_userunit_rotated_pdf_keeps_visible_physical_size(tmp_path):
    source = make_pdf(tmp_path / "scaled.pdf", ("VISIBLE",))
    reader = PdfReader(source)
    writer = PdfWriter()
    page = writer.add_page(reader.pages[0])
    page.cropbox = RectangleObject([20, 20, 280, 400])
    page[NameObject("/UserUnit")] = NumberObject(2)
    page.rotate(90)
    writer.write(source)
    original = engine.build_pdf(engine.inspect_file(source))
    page = PdfReader(BytesIO(original.data)).pages[0]
    assert page.get("/UserUnit") == 2
    assert list(page.cropbox) == [20, 20, 280, 400]
    assert page.rotation == 90
    raster = engine._rasterize(original.data, 96, 75, None, None)
    actual = PdfReader(BytesIO(raster)).pages[0]
    assert float(actual.mediabox.width) == pytest.approx(760, abs=0.01)
    assert float(actual.mediabox.height) == pytest.approx(520, abs=0.01)
    assert actual.rotation == 0
    before = engine.render_pdf(original.data, max_size=(760, 520))
    after = engine.render_pdf(raster, max_size=(760, 520))
    assert before.size == after.size
    # Compare substantial dark text, rather than only blank-page dimensions.
    before_dark = sum(1 for pixel in before.convert("L").tobytes() if pixel < 100)
    after_dark = sum(1 for pixel in after.convert("L").tobytes() if pixel < 100)
    assert before_dark > 100
    assert abs(before_dark - after_dark) / before_dark < 0.1


def test_noninteractive_annotations_preserved_in_original_and_flattened_in_raster(tmp_path):
    source = make_pdf(tmp_path / "annotated.pdf", ("ONE",))
    reader = PdfReader(source)
    writer = PdfWriter()
    page = writer.add_page(reader.pages[0])
    annotation = DictionaryObject({NameObject("/Type"): NameObject("/Annot"),
                                   NameObject("/Subtype"): NameObject("/Text"),
                                   NameObject("/Rect"): RectangleObject([30, 30, 50, 50]),
                                   NameObject("/Contents"): TextStringObject("Synthetic note")})
    page[NameObject("/Annots")] = writer._add_object(ArrayObject([writer._add_object(annotation)]))
    writer.write(source)
    original = engine.build_pdf(engine.inspect_file(source))
    anns = PdfReader(BytesIO(original.data)).pages[0]["/Annots"]
    assert anns[0].get_object()["/Contents"] == "Synthetic note"
    raster = engine._rasterize(original.data, 96, 75, None, None)
    assert "/Annots" not in PdfReader(BytesIO(raster)).pages[0]


@pytest.mark.parametrize("value", [0, -1, 100000])
def test_invalid_or_extreme_userunit_rejected(tmp_path, value):
    source = make_pdf(tmp_path / "invalid-scale.pdf", ("ONE",))
    writer = PdfWriter(clone_from=source)
    writer.pages[0][NameObject("/UserUnit")] = NumberObject(value)
    writer.write(source)
    with pytest.raises(engine.DocumentError, match="scaling"):
        engine.inspect_file(source)


def test_job_source_limit_and_repeated_source_not_double_counted(tmp_path, monkeypatch):
    first = make_pdf(tmp_path / "first.pdf", ("ONE",))
    second = make_pdf(tmp_path / "second.pdf", ("TWO",))
    first_pages = engine.inspect_file(first)
    second_pages = engine.inspect_file(second)
    monkeypatch.setattr(engine, "MAX_TOTAL_SOURCE_BYTES", len(first.read_bytes()) + 10)
    assert engine.build_pdf(first_pages * 3).page_count == 3
    with pytest.raises(engine.DocumentError, match="512 MB job limit"):
        engine.build_pdf(first_pages + second_pages)


def test_same_bytes_at_separate_paths_are_independent_inputs(tmp_path):
    first = make_pdf(tmp_path / "first.pdf", ("SAME",))
    second = tmp_path / "second.pdf"
    second.write_bytes(first.read_bytes())
    left, right = engine.inspect_file(first)[0], engine.inspect_file(second)[0]
    assert left.fingerprint == right.fingerprint
    assert left.source != right.source
    assert text(engine.build_pdf([left, right]).data) == ["SAME", "SAME"]


def test_certification_signature_root_rejected(tmp_path):
    source = make_pdf(tmp_path / "certified.pdf", ("ONE",))
    writer = PdfWriter(clone_from=source)
    writer._root_object[NameObject("/Perms")] = DictionaryObject({
        NameObject("/DocMDP"): writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Sig")}))})
    writer.write(source)
    with pytest.raises(engine.DocumentError, match="certified or digitally signed"):
        engine.inspect_file(source)
