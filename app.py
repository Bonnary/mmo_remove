from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from controllers.main_controller import MainController
from services.demucs_service import DemucsService
from services.export_service import ExportService
from services.ffmpeg_service import FFmpegService
from utils.signals import AppSignals

LOG_PATH = Path(tempfile.gettempdir()) / "mmo_remove.log"


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.getLogger(__name__).info("Log file: %s", LOG_PATH)


class VideoEditorApp:
    def __init__(self, argv: list[str]) -> None:
        _setup_logging()
        self._qapp = QApplication(argv)
        self._signals = AppSignals()
        self._ffmpeg_service = FFmpegService()
        self._demucs_service = DemucsService()
        self._export_service = ExportService(
            ffmpeg_service=self._ffmpeg_service,
            demucs_service=self._demucs_service,
            signals=self._signals,
        )
        self._controller = MainController(
            ffmpeg_service=self._ffmpeg_service,
            export_service=self._export_service,
            signals=self._signals,
        )

    def run(self) -> int:
        self._controller.show()
        return self._qapp.exec()
