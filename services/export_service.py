from __future__ import annotations

import logging
import shutil
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from models.edit_settings import EditSettings
from models.video_item import VideoItem
from services.demucs_service import DemucsService
from services.ffmpeg_service import FFmpegService
from utils.signals import AppSignals

log = logging.getLogger(__name__)


class ExportWorker(QThread):
    progress = Signal(float)
    finished = Signal()
    error = Signal(str)
    status_update = Signal(str)

    def __init__(
        self,
        video: VideoItem,
        settings: EditSettings,
        output_dir: Path,
        ffmpeg_service: FFmpegService,
        demucs_service: DemucsService,
    ) -> None:
        super().__init__()
        self.video = video
        self.settings = settings
        self.output_dir = output_dir
        self._ffmpeg = ffmpeg_service
        self._demucs = demucs_service

    def run(self) -> None:
        demucs_output_dir = self.video.path.parent / ".demucs_output"
        try:
            self.video.status = "processing"
            audio_path: Path | None = None
            has_demucs = self.settings.remove_music

            # ── Phase 1: Demucs (0 → 45%) ─────────────────────────────
            if has_demucs:
                self.status_update.emit("Extracting audio…")
                self.progress.emit(0.02)

                # Heartbeat animates the bar while demucs blocks
                _pct = [0.0]
                _done = threading.Event()

                def _heartbeat() -> None:
                    while not _done.wait(0.4):
                        _pct[0] = min(_pct[0] + 0.012, 0.88)
                        self.progress.emit(_pct[0] * 0.45)
                        self.status_update.emit(
                            f"Removing music… {int(_pct[0] * 100)}%"
                        )

                hb = threading.Thread(target=_heartbeat, daemon=True)
                hb.start()
                try:
                    audio_path = self._demucs.extract_vocals(self.video.path)
                    self.video.vocals_path = audio_path
                finally:
                    _done.set()
                    hb.join()

                self.progress.emit(0.45)
                self.status_update.emit("Music removed — encoding…")

            # ── Phase 2: FFmpeg encode (45 → 100% or 0 → 100%) ────────
            output_path = self.output_dir / f"{self.video.path.stem}_edited.mp4"

            if not has_demucs:
                self.status_update.emit("Encoding…")

            def on_progress(p: float) -> None:
                overall = (0.45 + p * 0.55) if has_demucs else p
                self.progress.emit(overall)
                self.status_update.emit(f"Encoding… {int(p * 100)}%")

            self._ffmpeg.run_ffmpeg(
                input_path=self.video.path,
                output_path=output_path,
                settings=self.settings,
                video_w=self.video.width,
                video_h=self.video.height,
                duration=self.video.duration,
                audio_path=audio_path,
                progress_callback=on_progress,
            )
            self.video.status = "done"
            self.video.progress = 1.0
            self.finished.emit()
        except Exception as e:
            full_error = traceback.format_exc()
            log.error("Export failed for %s:\n%s", self.video.path, full_error)
            self.video.status = "error"
            self.video.error_msg = str(e)
            self.error.emit(str(e))
        finally:
            if demucs_output_dir.exists():
                shutil.rmtree(demucs_output_dir, ignore_errors=True)


class ExportService:
    def __init__(
        self,
        ffmpeg_service: FFmpegService,
        demucs_service: DemucsService,
        signals: AppSignals,
    ) -> None:
        self._ffmpeg = ffmpeg_service
        self._demucs = demucs_service
        self._signals = signals
        self._videos: list[VideoItem] = []
        self._settings: EditSettings | None = None
        self._output_dir: Path | None = None
        self._current_index = 0
        self._worker: ExportWorker | None = None

    def export_batch(
        self,
        videos: list[VideoItem],
        settings: EditSettings,
        output_dir: Path,
    ) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait()
        self._videos = videos
        self._settings = settings
        self._output_dir = output_dir
        self._current_index = 0
        self._start_next()

    def _start_next(self) -> None:
        if self._worker is not None:
            try:
                self._worker.progress.disconnect(self._on_progress)
                self._worker.finished.disconnect(self._on_finished)
                self._worker.error.disconnect(self._on_error)
                self._worker.status_update.disconnect(self._signals.status_message)
            except RuntimeError:
                pass
            self._worker = None

        if self._current_index >= len(self._videos):
            self._signals.export_all_done.emit()
            return

        video = self._videos[self._current_index]
        assert self._settings is not None
        assert self._output_dir is not None

        self._worker = ExportWorker(
            video=video,
            settings=self._settings,
            output_dir=self._output_dir,
            ffmpeg_service=self._ffmpeg,
            demucs_service=self._demucs,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.status_update.connect(self._signals.status_message)
        self._worker.start()

    def _on_progress(self, p: float) -> None:
        video = self._videos[self._current_index]
        video.progress = p
        self._signals.export_progress.emit(str(video.path), p)

    def _on_finished(self) -> None:
        video = self._videos[self._current_index]
        self._signals.export_finished.emit(str(video.path))
        self._current_index += 1
        self._start_next()

    def _on_error(self, msg: str) -> None:
        video = self._videos[self._current_index]
        self._signals.export_error.emit(str(video.path), msg)
        self._current_index += 1
        self._start_next()

    def cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait()
        self._current_index = len(self._videos)
