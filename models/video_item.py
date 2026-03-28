from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VideoItem:
    path: Path
    filename: str
    duration: float  # seconds
    width: int
    height: int
    fps: float
    codec_name: str = ""

    status: str = "pending"  # pending | processing | done | error
    progress: float = 0.0  # 0.0-1.0
    error_msg: str = ""
    vocals_path: Path | None = field(default=None, repr=False)
