"""A compact toolbar that wraps complete controls at the system font size."""
from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtWidgets import QLayout, QSpacerItem


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def addStretch(self):
        self.addItem(QSpacerItem(0, 0))

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect, move):
        x, y, row_height = rect.x(), rect.y(), 0
        visible = [item for item in self._items if item.spacerItem() or not item.isEmpty()]
        for index, item in enumerate(visible):
            if item.spacerItem():
                following = visible[index + 1:]
                controls = [entry for entry in following if not entry.spacerItem()]
                needed = sum(entry.sizeHint().width() for entry in controls)
                needed += max(0, len(controls) - 1) * self.spacing()
                gaps = 1 + sum(bool(entry.spacerItem()) for entry in following)
                x += max(0, rect.right() + 1 - x - needed) // gaps
                continue
            size = item.sizeHint()
            if x > rect.x() and x + size.width() > rect.right() + 1:
                x, y, row_height = rect.x(), y + row_height + self.spacing(), 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + self.spacing()
            row_height = max(row_height, size.height())
        return y + row_height - rect.y()
