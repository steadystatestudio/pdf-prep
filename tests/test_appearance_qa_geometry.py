"""Package QA distinguishes clipped content from genuinely persistent controls."""

import importlib
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea, QWidget

SOURCE = next((Path(__file__).parents[1] / "src").glob("*/appearance_qa.py")).parent
QA = importlib.import_module(SOURCE.name + ".appearance_qa")


@pytest.fixture
def window():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.resize(700, 600)
    window.work_scroll = QScrollArea(window)
    window.work_scroll.setGeometry(40, 90, 620, 400)
    content = QWidget()
    content.resize(600, 1000)
    window.work_scroll.setWidget(content)
    window.extract_button = QPushButton("Content action", content)
    window.extract_button.setGeometry(0, 440, 160, 30)
    window.prepare_button = QPushButton("Persistent task action", window)
    window.prepare_button.setGeometry(40, 520, 220, 30)
    window.status_label = QLabel("Persistent status", window)
    window.status_label.setGeometry(40, 560, 220, 25)
    window.show()
    app.processEvents()
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()


def test_clipped_scroll_content_is_not_a_persistent_control(window):
    assert (
        window.extract_button.isVisible()
        and window.extract_button.visibleRegion().isEmpty()
    )
    report = QA._persistent_workspace(window, QRect(0, 0, 800, 800))
    names = {item["name"] for item in report["persistent_controls"]}
    assert names == {"prepare_button", "status_label"}
    assert report["workspace"][3] >= report["minimum_workspace_height"]


def test_real_persistent_overlap_still_fails(window):
    window.status_label.move(window.prepare_button.pos())
    with pytest.raises(AssertionError, match="persistent-controls-overlap"):
        QA._persistent_workspace(window, QRect(0, 0, 800, 800))
