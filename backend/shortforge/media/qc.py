"""QC рендера: ffprobe (длительность/разрешение/fps) + ebur128 (integrated LUFS).

Результат кладётся в Render.qc: {"lufs":-14.1,"duration":34.2,"width":1080,...}.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from .ffutil import FFMPEG, video_info

log = logging.getLogger("shortforge.media.qc")


def measure_lufs(path: Path | str) -> float | None:
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
           "-af", "ebur128=framelog=quiet", "-f", "null", "-"]
    log.info("exec: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    matches = re.findall(r"I:\s+(-?[\d.]+)\s+LUFS", proc.stderr)
    if not matches:
        return None
    return float(matches[-1])


def probe_qc(path: Path | str) -> dict:
    """Собирает QC-словарь мастер/rough файла."""
    info = video_info(path)
    lufs = measure_lufs(path)
    qc = {
        "duration": round(info["duration"], 2),
        "width": info["width"],
        "height": info["height"],
        "fps": info["fps"],
        "lufs": round(lufs, 1) if lufs is not None else None,
    }
    qc["resolution_ok"] = info["width"] == 1080 and info["height"] == 1920
    qc["duration_ok"] = 15.0 <= info["duration"] <= 45.0
    qc["lufs_ok"] = lufs is not None and abs(lufs - (-14.0)) <= 1.5
    return qc
