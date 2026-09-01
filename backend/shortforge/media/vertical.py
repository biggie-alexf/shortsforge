"""Кроп 16:9 -> 9:16 и выбор сниппет-кандидатов по motion score.

Motion score: PySceneDetect ContentDetector, при недоступности — cv2-диффы,
при недоступности и этого — равные окна с псевдо-оценкой (детерминированно).
"""
from __future__ import annotations

import logging
from pathlib import Path

from .ffutil import media_duration, run_ffmpeg

log = logging.getLogger("shortforge.media.vertical")


def crop_vertical(
    src: Path | str,
    out: Path | str,
    *,
    start: float = 0.0,
    duration: float | None = None,
    fps: int = 30,
    zoom: bool = False,
) -> Path:
    """Центр-кроп 16:9 -> 9:16 (1080x1920), без звука.

    zoom=True добавляет лёгкий наезд (zoompan) — «центр + чуть zoompan, не усложняем».
    """
    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = "crop=w=ih*9/16:h=ih:x=(iw-ow)/2:y=0,scale=1080:1920"
    if zoom:
        vf += (
            f",zoompan=z='min(1.10,1+0.0012*in)':x='(iw-iw/zoom)/2':"
            f"y='(ih-ih/zoom)/2':d=1:s=1080x1920:fps={fps}"
        )
    args: list[str] = ["-ss", f"{start:.3f}", "-i", str(src)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += [
        "-vf", vf, "-r", str(fps), "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        str(out),
    ]
    run_ffmpeg(args)
    return out


# ------------------------------------------------------------------ motion

def _motion_scores_cv2(src: Path, windows: list[tuple[float, float]]) -> list[float]:
    """Средний абсолютный межкадровый дифф по ~6 сэмплам на окно (downscale 160px)."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError(f"cv2 cannot open {src}")
    scores = []
    for w_start, w_dur in windows:
        samples = []
        prev = None
        for i in range(6):
            t = w_start + w_dur * i / 6.0
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok:
                break
            small = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY)
            if prev is not None:
                samples.append(float(np.mean(cv2.absdiff(small, prev))))
            prev = small
        scores.append(round(sum(samples) / len(samples), 3) if samples else 0.0)
    cap.release()
    return scores


def _motion_scores_scenedetect(src: Path, windows: list[tuple[float, float]]) -> list[float]:
    """ContentDetector: плотность контент-переходов + cv2-дифф как компонента."""
    from scenedetect import ContentDetector, detect  # noqa: PLC0415

    scenes = detect(str(src), ContentDetector(threshold=27.0))
    cuts = [s[0].get_seconds() for s in scenes]
    base = _motion_scores_cv2(src, windows)
    out = []
    for (w_start, w_dur), b in zip(windows, base):
        n_cuts = sum(1 for c in cuts if w_start <= c < w_start + w_dur)
        out.append(round(b + n_cuts * 2.0, 3))
    return out


def snippet_windows(
    src: Path | str, *, count: int = 3, min_d: float = 3.0, max_d: float = 5.0
) -> list[tuple[float, float, float]]:
    """Возвращает до count окон (start, duration, motion_score), отсортированных по score.

    Кандидатные окна — равномерная сетка 2*count по файлу, длительность 3–5 с.
    """
    src = Path(src)
    total = media_duration(src)
    if total <= 0:
        return []
    dur = min(max_d, max(min_d, total / (count * 2 + 1)))
    n_grid = max(count, min(count * 2, int(total // dur)))
    if n_grid <= 0:
        n_grid = 1
        dur = min(max_d, max(0.5, total))
    step = max(0.0, (total - dur)) / max(1, n_grid - 1) if n_grid > 1 else 0.0
    windows = [(round(i * step, 3), round(dur, 3)) for i in range(n_grid)]

    try:
        scores = _motion_scores_scenedetect(src, windows)
    except Exception as e:  # noqa: BLE001
        log.info("scenedetect unavailable (%s), trying cv2", e)
        try:
            scores = _motion_scores_cv2(src, windows)
        except Exception as e2:  # noqa: BLE001
            log.info("cv2 unavailable (%s), pseudo scores", e2)
            scores = [round(0.5 + 0.01 * (i % 7), 3) for i in range(len(windows))]

    ranked = sorted(zip(windows, scores), key=lambda p: (-p[1], p[0][0]))
    return [(w[0], w[1], s) for w, s in ranked[:count]]
