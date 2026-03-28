from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QFile, QThread, QUrl, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QFileDialog,
    QGraphicsScene,
    QMainWindow,
    QMessageBox,
    QWidget,
)

from models.video_item import VideoItem
from utils.signals import AppSignals
from views.overlay_item import OverlayItem


class _AV1TranscodeWorker(QThread):
    """Transcode an AV1 video to a temp H.264 file for Qt preview."""

    done = Signal(str)   # emits temp file path on success
    error = Signal(str)  # emits error message on failure

    def __init__(self, src: Path) -> None:
        super().__init__()
        self._src = src

    def run(self) -> None:
        tmp = tempfile.mktemp(suffix=".mp4")
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(self._src),
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                tmp,
            ],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode == 0:
            self.done.emit(tmp)
        else:
            self.error.emit(result.stderr.decode(errors="replace"))


class MainWindow(QMainWindow):
    add_videos_requested = Signal()
    remove_video_requested = Signal(int)
    export_requested = Signal()
    settings_changed = Signal(str, object)
    overlay_position_changed = Signal(float, float, float, float)
    video_selected = Signal(int)

    def __init__(self, signals: AppSignals, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app_signals = signals
        self._overlay_item: OverlayItem | None = None

        self._load_ui()
        self._setup_preview()
        self._connect_ui()
        self._connect_app_signals()

        self.setWindowTitle("MMO Remove - Video Editor")
        self.resize(1200, 800)

    def _load_ui(self) -> None:
        ui_path = Path(__file__).parent.parent / "resources" / "ui" / "main_window.ui"
        loader = QUiLoader()
        ui_file = QFile(str(ui_path))
        ui_file.open(QFile.ReadOnly)  # type: ignore[arg-type]
        self._ui = loader.load(ui_file, None)
        ui_file.close()
        self.setCentralWidget(self._ui)

    def _setup_preview(self) -> None:
        self._scene = QGraphicsScene()
        self._video_item = QGraphicsVideoItem()
        self._scene.addItem(self._video_item)
        self._ui.graphicsView.setScene(self._scene)

        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_item)

        self._transcode_worker: _AV1TranscodeWorker | None = None
        self._preview_tmp: str | None = None
        self._current_video: VideoItem | None = None

        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._video_item.nativeSizeChanged.connect(self._on_native_size_changed)

    def _connect_ui(self) -> None:
        self._ui.btnAddVideos.clicked.connect(self.add_videos_requested.emit)
        self._ui.btnRemoveVideo.clicked.connect(
            lambda: self.remove_video_requested.emit(
                self._ui.listVideos.currentRow()
            )
        )
        self._ui.btnExport.clicked.connect(self._on_export_clicked)
        self._ui.btnAddOverlay.clicked.connect(self._on_add_overlay)
        self._ui.btnRemoveOverlay.clicked.connect(self._on_remove_overlay)
        self._ui.btnPlayPause.clicked.connect(self._toggle_play)

        # Settings controls
        self._ui.chkRemoveMusic.toggled.connect(
            lambda v: self.settings_changed.emit("remove_music", v)
        )
        self._ui.comboRotation.currentTextChanged.connect(
            lambda t: self.settings_changed.emit(
                "rotation", int(t.replace("°", ""))
            )
        )
        self._ui.comboAspect.currentTextChanged.connect(
            lambda t: self.settings_changed.emit(
                "target_aspect", None if t == "Original" else t
            )
        )
        self._ui.chkFlipH.toggled.connect(
            lambda v: self.settings_changed.emit("flip_h", v)
        )
        self._ui.chkFlipV.toggled.connect(
            lambda v: self.settings_changed.emit("flip_v", v)
        )
        self._ui.sliderSpeed.valueChanged.connect(self._on_speed_changed)
        self._ui.spinTrimStart.valueChanged.connect(
            lambda v: self.settings_changed.emit("trim_start", v)
        )
        self._ui.spinTrimEnd.valueChanged.connect(
            lambda v: self.settings_changed.emit(
                "trim_end", v if v > 0 else None
            )
        )

        # Seek slider
        self._ui.sliderSeek.sliderMoved.connect(
            lambda v: self._player.setPosition(v)
        )

        # Video list selection
        self._ui.listVideos.currentRowChanged.connect(self.video_selected.emit)

    def _connect_app_signals(self) -> None:
        self._export_had_errors = False
        self._export_errors: list[str] = []
        self._app_signals.export_progress.connect(self._on_export_progress)
        self._app_signals.export_finished.connect(self._on_export_finished)
        self._app_signals.export_error.connect(self._on_export_error)
        self._app_signals.export_all_done.connect(self._on_all_exports_done)
        self._app_signals.status_message.connect(self._ui.lblExportStatus.setText)

    # ── Preview ────────────────────────────────────────────────────────

    def load_preview(self, video: VideoItem) -> None:
        self._current_video = video
        if video.codec_name == "av1":
            self._ui.lblStatus.setText(
                f"Transcoding {video.filename} for preview (AV1 → H.264)…"
            )
            self._player.stop()
            self._transcode_worker = _AV1TranscodeWorker(video.path)
            self._transcode_worker.done.connect(self._on_preview_transcode_done)
            self._transcode_worker.error.connect(self._on_preview_transcode_error)
            self._transcode_worker.start()
        else:
            self._load_preview_path(video.path, video)

    def _load_preview_path(self, path: Path, video: VideoItem) -> None:
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()
        self._player.pause()
        self._ui.lblStatus.setText(
            f"{video.filename} — {video.width}x{video.height}, "
            f"{video.duration:.1f}s, {video.fps:.1f}fps"
        )

    def _on_preview_transcode_done(self, tmp_path: str) -> None:
        # Clean up previous temp file if any
        if self._preview_tmp and self._preview_tmp != tmp_path:
            try:
                Path(self._preview_tmp).unlink(missing_ok=True)
            except OSError:
                pass
        self._preview_tmp = tmp_path
        if self._current_video:
            self._load_preview_path(Path(tmp_path), self._current_video)

    def _on_preview_transcode_error(self, msg: str) -> None:
        self._ui.lblStatus.setText(
            f"AV1 preview failed: {msg[:120]}"
        )

    def _on_native_size_changed(self, size) -> None:
        if size.width() > 0 and size.height() > 0:
            self._scene.setSceneRect(0, 0, size.width(), size.height())
            self._video_item.setSize(size)
            self._ui.graphicsView.fitInView(
                self._scene.sceneRect(),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            if self._overlay_item:
                self._overlay_item.set_scene_size(size.width(), size.height())

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._ui.btnPlayPause.setText("Play")
        else:
            self._player.play()
            self._ui.btnPlayPause.setText("Pause")

    def _on_position_changed(self, pos: int) -> None:
        self._ui.sliderSeek.setValue(pos)
        self._ui.lblTime.setText(self._format_time(pos))

    def _on_duration_changed(self, dur: int) -> None:
        self._ui.sliderSeek.setRange(0, dur)
        self._ui.lblDuration.setText(self._format_time(dur))

    # ── Overlay ────────────────────────────────────────────────────────

    def _on_add_overlay(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Overlay Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if not path:
            return

        pixmap = QPixmap(path)
        if pixmap.isNull():
            return

        # Remove old overlay if present
        if self._overlay_item:
            self._scene.removeItem(self._overlay_item)

        scene_rect = self._scene.sceneRect()
        sw = scene_rect.width() if scene_rect.width() > 0 else 640
        sh = scene_rect.height() if scene_rect.height() > 0 else 360

        self._overlay_item = OverlayItem(pixmap, sw, sh)
        self._overlay_item.set_from_fractions(0.1, 0.1, 0.2, 0.2)
        self._scene.addItem(self._overlay_item)
        self._overlay_item.setSelected(True)

        self._overlay_item.signals.position_changed.connect(
            self.overlay_position_changed.emit
        )

        self.settings_changed.emit("overlay_path", path)
        x, y, w, h = self._overlay_item.get_fractional_rect()
        self.overlay_position_changed.emit(x, y, w, h)

        self._ui.btnRemoveOverlay.setEnabled(True)

    def _on_remove_overlay(self) -> None:
        if self._overlay_item:
            self._scene.removeItem(self._overlay_item)
            self._overlay_item = None
        self.settings_changed.emit("overlay_path", None)
        self._ui.btnRemoveOverlay.setEnabled(False)

    # ── Speed ──────────────────────────────────────────────────────────

    def _on_speed_changed(self, val: int) -> None:
        speed = val / 100.0
        self._ui.lblSpeedValue.setText(f"{speed:.2f}x")
        self.settings_changed.emit("speed", speed)

    # ── Video list ─────────────────────────────────────────────────────

    def update_video_list(self, videos: list[VideoItem]) -> None:
        self._ui.listVideos.clear()
        for v in videos:
            status = ""
            if v.status == "done":
                status = " [Done]"
            elif v.status == "error":
                status = " [Error]"
            elif v.status == "processing":
                status = f" [{int(v.progress * 100)}%]"
            self._ui.listVideos.addItem(
                f"{v.filename} ({v.duration:.1f}s){status}"
            )

    def _on_export_clicked(self) -> None:
        self._export_had_errors = False
        self._export_errors = []
        self._ui.btnExport.setEnabled(False)
        self._ui.progressBar.setValue(0)
        self.export_requested.emit()

    # ── Export progress ────────────────────────────────────────────────

    def _on_export_progress(self, path: str, progress: float) -> None:
        self._ui.progressBar.setValue(int(progress * 100))

    def _on_export_finished(self, path: str) -> None:
        pass  # status label updated via status_message; bar updated via export_progress

    def _on_export_error(self, path: str, error: str) -> None:
        self._export_had_errors = True
        self._export_errors.append(f"{Path(path).name}:\n{error}")
        self._ui.lblExportStatus.setText(f"Error: {Path(path).name} — {error[:80]}")

    def _on_all_exports_done(self) -> None:
        log_path = Path(tempfile.gettempdir()) / "mmo_remove.log"
        self._ui.progressBar.setValue(100)
        if not self._export_had_errors:
            self._ui.lblExportStatus.setText("All exports complete!")
            QMessageBox.information(self, "Export Complete", "All exports finished successfully!")
        else:
            self._ui.lblExportStatus.setText("Exports done — some files had errors.")
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Export Done")
            msg.setText("Some exports failed.")
            msg.setInformativeText(f"Full log: {log_path}")
            msg.setDetailedText("\n\n".join(self._export_errors))
            msg.exec()
        self._ui.btnExport.setEnabled(True)

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _format_time(ms: int) -> str:
        s = ms // 1000
        return f"{s // 60:02d}:{s % 60:02d}"
