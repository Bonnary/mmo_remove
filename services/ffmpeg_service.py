from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from models.edit_settings import EditSettings

log = logging.getLogger(__name__)


class FFmpegService:
    def __init__(self) -> None:
        self._encoder: str | None = None

    # ── GPU encoder detection ──────────────────────────────────────────

    def detect_encoder(self) -> str:
        if self._encoder:
            return self._encoder
        for enc in ("hevc_nvenc", "hevc_amf", "hevc_videotoolbox", "libx265"):
            ok = self._test_encoder(enc)
            log.info("Encoder test %s: %s", enc, "PASS" if ok else "fail")
            if ok:
                self._encoder = enc
                log.info("Selected encoder: %s", enc)
                return enc
        raise RuntimeError("No H.265 encoder available")

    @staticmethod
    def _test_encoder(encoder: str) -> bool:
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "color=black:s=256x256:d=0.04",
            "-c:v", encoder, "-frames:v", "1",
            "-f", "null", "-",
        ]
        try:
            r = subprocess.run(
                cmd, capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            return r.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    # ── Probe ──────────────────────────────────────────────────────────

    def probe(self, path: Path) -> dict:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ]
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if r.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {r.stderr}")
        data = json.loads(r.stdout)
        vs = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            None,
        )
        if not vs:
            raise RuntimeError(f"No video stream found in {path}")

        fps_str = vs.get("r_frame_rate", "30/1")
        num, den = fps_str.split("/")
        fps = float(num) / float(den) if float(den) else 30.0

        return {
            "duration": float(data.get("format", {}).get("duration", 0)),
            "width": int(vs.get("width", 0)),
            "height": int(vs.get("height", 0)),
            "fps": fps,
            "codec_name": vs.get("codec_name", ""),
        }

    # ── Filter chain construction ──────────────────────────────────────

    def build_filters(
        self,
        settings: EditSettings,
        video_w: int,
        video_h: int,
    ) -> tuple[list[str], list[str]]:
        vf: list[str] = []
        af: list[str] = []

        # 1 – Rotation
        if settings.rotation == 90:
            vf.append("transpose=1")
        elif settings.rotation == 180:
            vf.extend(["transpose=1", "transpose=1"])
        elif settings.rotation == 270:
            vf.append("transpose=2")

        # effective dims after rotation
        if settings.rotation in (90, 270):
            eff_w, eff_h = video_h, video_w
        else:
            eff_w, eff_h = video_w, video_h

        # 2 – Aspect ratio stretch
        if settings.target_aspect:
            aw, ah = (int(x) for x in settings.target_aspect.split(":"))
            target_h = int(eff_w * ah / aw)
            target_w = eff_w
            target_w += target_w % 2
            target_h += target_h % 2
            vf.append(f"scale={target_w}:{target_h}")

        # 3 – Flip
        if settings.flip_h:
            vf.append("hflip")
        if settings.flip_v:
            vf.append("vflip")

        # 4 – Speed
        if settings.speed != 1.0:
            vf.append(f"setpts={1.0 / settings.speed}*PTS")
            af.extend(self._build_atempo_chain(settings.speed))

        # 5 – FPS normalisation (always last)
        vf.append("fps=24")

        return vf, af

    @staticmethod
    def _build_atempo_chain(speed: float) -> list[str]:
        chain: list[str] = []
        remaining = speed
        while remaining > 2.0:
            chain.append("atempo=2.0")
            remaining /= 2.0
        while remaining < 0.5:
            chain.append("atempo=0.5")
            remaining /= 0.5
        chain.append(f"atempo={remaining}")
        return chain

    # ── Run ffmpeg ─────────────────────────────────────────────────────

    def run_ffmpeg(
        self,
        input_path: Path,
        output_path: Path,
        settings: EditSettings,
        video_w: int,
        video_h: int,
        duration: float,
        audio_path: Path | None = None,
        progress_callback: Callable[[float], None] | None = None,
    ) -> None:
        encoder = self.detect_encoder()
        vfilters, afilters = self.build_filters(settings, video_w, video_h)

        cmd: list[str] = ["ffmpeg", "-y"]

        # Hardware-accelerated decoding (NVIDIA CUDA)
        if encoder == "hevc_nvenc":
            cmd += ["-hwaccel", "cuda"]

        # Trim (input-level)
        if settings.trim_start > 0:
            cmd += ["-ss", str(settings.trim_start)]
        cmd += ["-i", str(input_path)]
        if settings.trim_end is not None:
            cmd += ["-t", str(settings.trim_end - settings.trim_start)]

        # Audio replacement
        audio_input_idx: int | None = None
        if audio_path:
            cmd += ["-i", str(audio_path)]
            audio_input_idx = 1

        # Overlay image input
        overlay_input_idx: int | None = None
        if settings.overlay_path:
            overlay_input_idx = (2 if audio_path else 1)
            cmd += ["-i", settings.overlay_path]

        # Build filter graph
        if settings.overlay_path and overlay_input_idx is not None:
            # Compute overlay pixel coords from fractions
            # Use effective dims after rotation + aspect stretch
            if settings.rotation in (90, 270):
                eff_w, eff_h = video_h, video_w
            else:
                eff_w, eff_h = video_w, video_h
            if settings.target_aspect:
                aw, ah = (int(x) for x in settings.target_aspect.split(":"))
                eff_h = int(eff_w * ah / aw)
                eff_w += eff_w % 2
                eff_h += eff_h % 2

            ox = int(settings.overlay_x * eff_w)
            oy = int(settings.overlay_y * eff_h)
            ow = max(2, int(settings.overlay_w * eff_w))
            oh = max(2, int(settings.overlay_h * eff_h))

            vf_str = ",".join(vfilters) if vfilters else "copy"
            fc = (
                f"[0:v]{vf_str}[base];"
                f"[{overlay_input_idx}:v]scale={ow}:{oh}[ovr];"
                f"[base][ovr]overlay={ox}:{oy}"
            )
            cmd += ["-filter_complex", fc]
        elif vfilters:
            cmd += ["-vf", ",".join(vfilters)]

        if afilters:
            cmd += ["-af", ",".join(afilters)]

        # Stream mapping
        if audio_path:
            if not settings.overlay_path:
                cmd += ["-map", "0:v", "-map", f"{audio_input_idx}:a"]
            else:
                cmd += ["-map", "[v]"] if False else []  # filter_complex auto-maps
                cmd += ["-map", f"{audio_input_idx}:a"]

        cmd += ["-c:v", encoder, "-c:a", "aac", "-b:a", "128k"]
        cmd += ["-progress", "pipe:1", "-nostats", str(output_path)]

        # Effective duration for progress calculation
        eff_duration = (settings.trim_end or duration) - settings.trim_start
        if settings.speed != 1.0:
            eff_duration /= settings.speed

        log.info("ffmpeg cmd: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

          # Drain stderr in background to prevent pipe buffer deadlock
        stderr_lines: list[str] = []

        def _drain_stderr() -> None:
            if proc.stderr:
                for line in proc.stderr:
                    stderr_lines.append(line)

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        if proc.stdout:
            for line in proc.stdout:
                if not progress_callback:
                    continue
                line = line.strip()
                if line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=")[1])
                        progress = min(us / (eff_duration * 1_000_000), 1.0)
                        progress_callback(max(0.0, progress))
                    except (ValueError, ZeroDivisionError):
                        pass

        proc.wait()
        stderr_thread.join()
        if proc.returncode != 0:
            stderr = "".join(stderr_lines)
            raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}): {stderr}")
