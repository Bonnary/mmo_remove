from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

log = logging.getLogger(__name__)

CHUNK_SECONDS = 300  # 5 minutes per chunk
_CFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _max_parallel() -> int:
    """Return how many concurrent demucs processes fit in available VRAM.

    Each process needs ~2 GB (1 GB model + ~1 GB processing buffers).
    Falls back to 1 if CUDA is unavailable or torch is not importable.
    """
    try:
        import torch
        if not torch.cuda.is_available():
            return 1
        free_bytes, _ = torch.cuda.mem_get_info()
        free_gb = free_bytes / (1024 ** 3)
        workers = max(1, int(free_gb // 2))
        log.info("VRAM free=%.1f GB → max_parallel=%d", free_gb, workers)
        return workers
    except Exception:
        return 1


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
            max_parallel = _max_parallel()
            log.info("Processing %d chunk(s), max %d parallel", len(chunks), max_parallel)

            # ── Step 3: process all chunks in parallel ─────────────────────
            with ThreadPoolExecutor(max_workers=max_parallel) as pool:
                futures = {pool.submit(self._run_chunk, c, output_dir): c for c in chunks}
                for fut in as_completed(futures):
                    fut.result()  # re-raises on any chunk failure

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
