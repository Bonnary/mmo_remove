from PySide6.QtCore import QObject, Signal


class AppSignals(QObject):
    export_progress = Signal(str, float)  # (video_path, 0.0-1.0)
    export_finished = Signal(str)  # (video_path)
    export_error = Signal(str, str)  # (video_path, error_message)
    export_all_done = Signal()

    demucs_started = Signal(str)  # (video_path)
    demucs_finished = Signal(str, str)  # (video_path, vocals_path)
    demucs_error = Signal(str, str)  # (video_path, error_message)

    status_message = Signal(str)
