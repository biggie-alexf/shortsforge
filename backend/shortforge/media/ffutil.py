"""Общие утилиты медиа-слоя: пути DATA_DIR, запуск ffmpeg/ffprobe.

ffmpeg вызывается subprocess-ом с явными аргументами (никаких shell=True),
команда логируется (CLAUDE.md).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger("shortforge.media")

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


def data_dir() -> Path:
    d = Path(os.environ.get("DATA_DIR", "/home/user/shortforge-data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def abs_path(rel: str) -> Path:
    """Путь в БД хранится относительно DATA_DIR."""
    p = Path(rel)
    return p if p.is_absolute() else data_dir() / p


def rel_path(p: Path | str) -> str:
    p = Path(p)
    try:
        return str(p.relative_to(data_dir()))
    except ValueError:
        return str(p)


def job_dir(batch_id: str, job_id: str, sub: str = "") -> Path:
    d = data_dir() / batch_id / job_id
    if sub:
        d = d / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_ffmpeg(args: list[str], *, tool: str = FFMPEG) -> subprocess.CompletedProcess:
    cmd = [tool, "-hide_banner", "-y", *[str(a) for a in args]] if tool == FFMPEG else [
        tool, *[str(a) for a in args]
    ]
    log.info("exec: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"{tool} failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-2000:]}"
        )
    return proc


def ffprobe_json(path: Path | str) -> dict:
    proc = run_ffmpeg(
        ["-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        tool=FFPROBE,
    )
    return json.loads(proc.stdout or "{}")


def media_duration(path: Path | str) -> float:
    info = ffprobe_json(path)
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def video_info(path: Path | str) -> dict:
    """{"duration","width","height","fps"} первого видеопотока."""
    info = ffprobe_json(path)
    out = {"duration": 0.0, "width": 0, "height": 0, "fps": 0.0}
    try:
        out["duration"] = float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        pass
    for st in info.get("streams", []):
        if st.get("codec_type") == "video":
            out["width"] = int(st.get("width") or 0)
            out["height"] = int(st.get("height") or 0)
            fr = st.get("avg_frame_rate") or "0/1"
            try:
                num, den = fr.split("/")
                out["fps"] = round(float(num) / float(den), 2) if float(den) else 0.0
            except (ValueError, ZeroDivisionError):
                pass
            break
    return out
