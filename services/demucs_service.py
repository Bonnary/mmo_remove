from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

CHUNK_SECONDS = 300  # 5 minutes per chunk
_CFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class DemucsService:
    def _run_chunk(self, chunk: Path, output_dir: Path) -> None:
        """Run demucs on one audio chunk, CUDA first then CPU fallback."""
        last_err = ""
        for device in ["cuda", "cpu"]:
            cmd = [
                sys.executable, "-m", "demucs",
                "--two-stems", "vocals",
                "--mp3",
                "-d", device,
                "-o", str(output_dir),
                str(chunk),
            ]
            log.info("demucs chunk=%s device=%s", chunk.name, device)
            r = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                creationflags=_CFLAGS,
            )
            if r.returncode == 0:
                log.info("demucs chunk=%s done on %s", chunk.name, device)
                return
            last_err = r.stderr
            log.warning("demucs chunk=%s device=%s failed: %s", chunk.name, device, last_err[-400:])
        raise RuntimeError(f"demucs failed for {chunk.name}: {last_err}")

    def extract_vocals(
        self, video_path: Path, output_dir: Path | None = None
    ) -> Path:
        if output_dir is None:
            output_dir = video_path.parent / ".demucs_output"
        output_dir.mkdir(parents=True, exist_ok=True)

        chunks_dir = output_dir / "chunks"
        tmp_audio = Path(tempfile.mktemp(suffix=".wav"))
        try:
            # ── Step 1: extract audio from video ──────────────────────────
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_path),
                 "-vn", "-ac", "2", "-ar", "44100", str(tmp_audio)],
                capture_output=True, text=True, encoding="utf-8",
                creationflags=_CFLAGS,
            )
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg audio extraction failed: {r.stderr}")

            # ── Step 2: split into CHUNK_SECONDS chunks ────────────────────
            chunks_dir.mkdir(exist_ok=True)
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(tmp_audio),
                 "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
                 "-c", "copy", str(chunks_dir / "chunk_%03d.wav")],
                capture_output=True, text=True, encoding="utf-8",
                creationflags=_CFLAGS,
            )
            if r.returncode != 0:
                raise RuntimeError(f"Audio split failed: {r.stderr}")

            chunks = sorted(chunks_dir.glob("chunk_*.wav"))
            if not chunks:
                raise RuntimeError("No audio chunks were created")
            log.info("Processing %d chunk(s) sequentially", len(chunks))

            # ── Step 3: process chunks one at a time ───────────────────────
            for chunk in chunks:
                self._run_chunk(chunk, output_dir)

        finally:
            tmp_audio.unlink(missing_ok=True)

        # ── Step 4: collect vocals in chunk order ──────────────────────────
        vocals: list[Path] = []
        for chunk in chunks:
            for ext in ("mp3", "wav"):
                matches = list(output_dir.rglob(f"{chunk.stem}/vocals.{ext}"))
                if matches:
                    vocals.append(matches[0])
                    break
            else:
                raise FileNotFoundError(f"Vocals not found for chunk {chunk.name}")

        if len(vocals) == 1:
            return vocals[0]

        # ── Step 5: concatenate vocals chunks ─────────────────────────────
        concat_txt = output_dir / "concat.txt"
        concat_txt.write_text(
            "\n".join(f"file '{v}'" for v in vocals), encoding="utf-8"
        )
        out_vocals = output_dir / "vocals.mp3"
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_txt), "-c", "copy", str(out_vocals)],
            capture_output=True, text=True, encoding="utf-8",
            creationflags=_CFLAGS,
        )
        if r.returncode != 0:
            raise RuntimeError(f"Vocal concatenation failed: {r.stderr}")

        return out_vocals
