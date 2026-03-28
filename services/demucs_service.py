from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class DemucsService:
    def extract_vocals(
        self, video_path: Path, output_dir: Path | None = None
    ) -> Path:
        if output_dir is None:
            output_dir = video_path.parent / ".demucs_output"

        output_dir.mkdir(parents=True, exist_ok=True)

        # Demucs requires audio input — extract audio from video first
        tmp_audio = Path(tempfile.mktemp(suffix=".wav"))
        try:
            extract_cmd = [
                "ffmpeg", "-y", "-i", str(video_path),
                "-vn", "-ac", "2", "-ar", "44100",
                str(tmp_audio),
            ]
            r = subprocess.run(
                extract_cmd, capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg audio extraction failed: {r.stderr}")

            # Try CUDA first; fall back to CPU if it fails (e.g. CPU-only PyTorch)
            last_error = ""
            for device in ["cuda", "cpu"]:
                cmd = [
                    sys.executable, "-m", "demucs",
                    "--two-stems", "vocals",
                    "--mp3",
                    "-d", device,
                    "-o", str(output_dir),
                    str(tmp_audio),
                ]
                log.info("Running demucs with device=%s", device)
                r = subprocess.run(
                    cmd, capture_output=True, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                if r.returncode == 0:
                    log.info("Demucs succeeded with device=%s", device)
                    break
                last_error = r.stderr
                log.warning("Demucs device=%s failed, stderr: %s", device, last_error[-300:])
            else:
                raise RuntimeError(f"demucs failed: {last_error}")
        finally:
            tmp_audio.unlink(missing_ok=True)

        # Search for vocals output under any model subfolder (.mp3 or .wav)
        for ext in ("mp3", "wav"):
            matches = list(output_dir.rglob(f"{tmp_audio.stem}/vocals.{ext}"))
            if matches:
                return matches[0]

        raise FileNotFoundError(f"Demucs vocals not found under {output_dir}")
