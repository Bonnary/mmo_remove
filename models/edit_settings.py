from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EditSettings:
    remove_music: bool = False

    overlay_path: str | None = None
    overlay_x: float = 0.0  # fraction of video width (0.0-1.0)
    overlay_y: float = 0.0  # fraction of video height (0.0-1.0)
    overlay_w: float = 0.2  # fraction of video width
    overlay_h: float = 0.2  # fraction of video height

    rotation: int = 0  # 0, 90, 180, 270

    target_aspect: str | None = None  # "16:9", "9:16", "4:3", "1:1", None=keep

    flip_h: bool = False
    flip_v: bool = False

    speed: float = 1.0  # 0.25 to 4.0

    trim_start: float = 0.0  # seconds
    trim_end: float | None = None  # None = end of video
