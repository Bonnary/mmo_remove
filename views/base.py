from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFile
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QWidget


class BaseView(QWidget):
    UI_FILE: str = ""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if self.UI_FILE:
            self._load_ui()

    def _load_ui(self) -> QWidget:
        ui_path = Path(__file__).parent.parent / "resources" / "ui" / self.UI_FILE
        loader = QUiLoader()
        ui_file = QFile(str(ui_path))
        ui_file.open(QFile.ReadOnly)  # type: ignore[arg-type]
        widget = loader.load(ui_file, self)
        ui_file.close()
        return widget
