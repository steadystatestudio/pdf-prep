"""Keep the explicit package check's retained release evidence verifiable."""
import hashlib
import json
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from pdf_prep.smoke import main


def test_package_smoke_retains_only_synthetic_relative_evidence(tmp_path):
    report = tmp_path / 'package-check.json'
    assert main(report) == 0, report.read_text(encoding='utf-8')
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['passed'] and data['synthetic_documents_only']
    assert str(tmp_path) not in report.read_text(encoding='utf-8')
    assert len(data['checks']) >= 10
    files = {Path(item['path']).name: item for item in data['artifacts']}
    for item in data['artifacts']:
        assert not Path(item['path']).is_absolute()
        path = tmp_path / item['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
        assert path.stat().st_size == item['size_bytes']
    for name, size in [('desktop-light.png', (1160, 820)),
                       ('desktop-dark.png', (1160, 820)),
                       ('narrow-dark.png', (640, 600))]:
        with Image.open(tmp_path / files[name]['path']) as image:
            assert image.size == size
    assert len(PdfReader(tmp_path / files['prepared-output.pdf']['path']).pages) == 3
    edited = PdfReader(tmp_path / files['extracted-rotated-output.pdf']['path'])
    assert [page.rotation for page in edited.pages] == [90, 90]
    assert [page.extract_text().strip() for page in edited.pages] == ['SECOND', 'FIRST']
    assert len(PdfReader(tmp_path / files['size-fit-output.pdf']['path']).pages) == 1
    assert not list(tmp_path.glob('pdf-prep-check-*'))
