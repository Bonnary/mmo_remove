from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

CHUNK_SECONDS = 300  # 5 minutes per chunk
_CFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_MIN_CHUNK_SECONDS = 10  # demucs pad1d fails on very short audio


class DemucsService:
    def _pad_if_short(self, chunk: Path) -> Path:
        """Return a silence-padded copy if chunk is shorter than _MIN_CHUNK_SECONDS.

        Demucs' pad1d asserts on very short audio (e.g. the last chunk of a long
        file). Padding to a minimum duration prevents the AssertionError.
        """
        padded = chunk.with_stem(chunk.stem + "_padded")
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(chunk),
             "-af", f"apad=whole_dur={_MIN_CHUNK_SECONDS}",
             str(padded)],
            capture_output=True, text=True, encoding="utf-8",
            creationflags=_CFLAGS,
        )
        if r.returncode != 0:
            log.warning("apad failed for %s, using original: %s", chunk.name, r.stderr[-200:])
            return chunk
        return padded

    def _run_chunk(self, chunk: Path, output_dir: Path) -> None:
        """Run demucs on one audio chunk, CUDA first then CPU fallback.

        If a padded copy was used, the output directory is renamed back to the
        original chunk stem so extract_vocals can find it.
        """
        input_path = self._pad_if_short(chunk)
        last_err = ""
        for device in ["cuda", "cpu"]:
            cmd = [
                sys.executable, "-m", "demucs",
                "--two-stems", "vocals",
                "--mp3",
                "-d", device,
                "-o", str(output_dir),
                str(input_path),
            ]
            log.info("demucs chunk=%s device=%s", chunk.name, device)
            r = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                creationflags=_CFLAGS,
            )
            if r.returncode == 0:
                log.info("demucs chunk=%s done on %s", chunk.name, device)
                # If we used a padded file, rename the output dir to the original stem
                if input_path != chunk:
                    for model_dir in output_dir.iterdir():
                        if not model_dir.is_dir():
                            continue
                        padded_out = model_dir / input_path.stem
                        original_out = model_dir / chunk.stem
                        if padded_out.exists() and not original_out.exists():
                            padded_out.rename(original_out)
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
