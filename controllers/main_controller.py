from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from models.edit_settings import EditSettings
from models.video_item import VideoItem
from services.export_service import ExportService
from services.ffmpeg_service import FFmpegService
from utils.signals import AppSignals
from views.main_window import MainWindow


class MainController:
    def __init__(
        self,
        ffmpeg_service: FFmpegService,
        export_service: ExportService,
        signals: AppSignals,
    ) -> None:
        self._ffmpeg = ffmpeg_service
        self._export = export_service
        self._signals = signals
        self._settings = EditSettings()
        self._videos: list[VideoItem] = []
        self._view = MainWindow(signals=signals)
        self._connect_signals()

    def _connect_signals(self) -> None:
        self._view.add_videos_requested.connect(self._on_add_videos)
        self._view.remove_video_requested.connect(self._on_remove_video)
        self._view.export_requested.connect(self._on_export)
        self._view.settings_changed.connect(self._on_settings_changed)
        self._view.overlay_position_changed.connect(self._on_overlay_moved)
        self._view.video_selected.connect(self._on_video_selected)
        self._signals.export_all_done.connect(self._on_export_all_done)

    # ── Video list ─────────────────────────────────────────────────────

    def _on_add_videos(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self._view, "Select Videos", "",
            "Videos (*.mp4 *.avi *.mov *.mkv *.webm *.flv *.wmv)",
        )
        if not paths:
            return

        for p in paths:
            path = Path(p)
            try:
                info = self._ffmpeg.probe(path)
                video = VideoItem(
                    path=path,
                    filename=path.name,
                    duration=info["duration"],
                    width=info["width"],
                    height=info["height"],
                    fps=info["fps"],
                    codec_name=info["codec_name"],
                )
                self._videos.append(video)
            except RuntimeError as e:
                self._signals.status_message.emit(f"Error loading {path.name}: {e}")

        self._view.update_video_list(self._videos)
        if self._videos:
            self._view.load_preview(self._videos[-1])

    def _on_remove_video(self, index: int) -> None:
        if 0 <= index < len(self._videos):
            self._videos.pop(index)
            self._view.update_video_list(self._videos)

    def _on_video_selected(self, index: int) -> None:
        if 0 <= index < len(self._videos):
            self._view.load_preview(self._videos[index])

    # ── Settings ───────────────────────────────────────────────────────

    def _on_settings_changed(self, key: str, value: object) -> None:
        setattr(self._settings, key, value)

    def _on_overlay_moved(
        self, x: float, y: float, w: float, h: float
    ) -> None:
        self._settings.overlay_x = x
        self._settings.overlay_y = y
        self._settings.overlay_w = w
        self._settings.overlay_h = h

    # ── Export ─────────────────────────────────────────────────────────

    def _on_export(self) -> None:
        if not self._videos:
            self._signals.status_message.emit("No videos to export")
            return

        output_dir = QFileDialog.getExistingDirectory(
            self._view, "Select Output Folder"
        )
        if not output_dir:
            return

        # Reset status
        for v in self._videos:
            v.status = "pending"
            v.progress = 0.0
            v.error_msg = ""

        self._view.update_video_list(self._videos)
        self._export.export_batch(
            self._videos, self._settings, Path(output_dir)
        )

    def _on_export_all_done(self) -> None:
        self._view.update_video_list(self._videos)

    # ── Public ─────────────────────────────────────────────────────────

    def show(self) -> None:
        self._view.show()
