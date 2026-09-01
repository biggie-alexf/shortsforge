"""Синтетические «летсплеи» и звуковые фикстуры (ADR-011).

Генерируются в {DATA_DIR}/_fixtures:
- letsplay_1..3.mp4 — 1920x1080, 60 с, testsrc2 + движущиеся drawbox-«аватары»;
- music.wav        — 30 с эмбиент-дрон для мастера;
- sfx/{impact,tick,sting,glitch}.wav — акцентные SFX.

Вызывается из scripts/make_fixtures.py и лениво из MockYouTube.
"""
from __future__ import annotations

from pathlib import Path

from .ffutil import data_dir, run_ffmpeg

FIXTURE_COUNT = 3
FIXTURE_DURATION = 60.0
SFX_NAMES = ("impact", "tick", "sting", "glitch")


def fixtures_dir() -> Path:
    d = data_dir() / "_fixtures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def letsplay_path(n: int) -> Path:
    return fixtures_dir() / f"letsplay_{n}.mp4"


def music_path() -> Path:
    return fixtures_dir() / "music.wav"


def sfx_path(name: str) -> Path:
    return fixtures_dir() / "sfx" / f"{name}.wav"


def _make_letsplay(n: int, out: Path) -> None:
    """testsrc2 + 3 «аватара» с разными траекториями; hue-сдвиг отличает видео."""
    hue = (n - 1) * 55
    speed = 90 + n * 40
    vf = (
        f"hue=h={hue},"
        f"drawbox=x='mod(120+t*{speed},1700)':y='mod(90+t*55,860)':w=150:h=190:"
        f"color=red@0.85:t=fill,"
        f"drawbox=x='mod(880+t*{speed + 70},1740)':y='mod(420+t*95,880)':w=120:h=160:"
        f"color=blue@0.85:t=fill,"
        f"drawbox=x='mod(500+t*{speed + 130},1760)':y='mod(240+t*70,900)':w=100:h=140:"
        f"color=yellow@0.85:t=fill"
    )
    run_ffmpeg(
        [
            "-f", "lavfi",
            "-i", f"testsrc2=size=1920x1080:rate=30:duration={FIXTURE_DURATION}",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-g", "30", "-pix_fmt", "yuv420p", "-an",
            str(out),
        ]
    )


def _make_music(out: Path) -> None:
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", "sine=frequency=110:duration=30",
            "-f", "lavfi", "-i", "sine=frequency=164.8:duration=30",
            "-f", "lavfi", "-i", "anoisesrc=d=30:c=pink:a=0.05",
            "-filter_complex",
            "[0:a][1:a][2:a]amix=inputs=3:normalize=1,lowpass=f=700,"
            "tremolo=f=0.15:d=0.4,volume=0.7,aformat=sample_rates=44100:channel_layouts=mono",
            str(out),
        ]
    )


def _make_sfx(name: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    graphs = {
        "impact": (
            ["-f", "lavfi", "-i", "sine=frequency=55:duration=0.4"],
            "volume=2.5,afade=t=out:st=0.05:d=0.35",
        ),
        "tick": (
            ["-f", "lavfi", "-i", "sine=frequency=1400:duration=0.07"],
            "volume=1.2,afade=t=out:st=0.02:d=0.05",
        ),
        "sting": (
            ["-f", "lavfi", "-i", "sine=frequency=880:duration=0.5"],
            "tremolo=f=9:d=0.8,afade=t=out:st=0.1:d=0.4",
        ),
        "glitch": (
            ["-f", "lavfi", "-i", "anoisesrc=d=0.3:c=white:a=0.6"],
            "tremolo=f=25:d=1,afade=t=out:st=0.05:d=0.25",
        ),
    }
    inputs, flt = graphs[name]
    run_ffmpeg(
        [*inputs, "-af", flt + ",aformat=sample_rates=44100:channel_layouts=mono", str(out)]
    )


def ensure_fixtures(force: bool = False) -> list[Path]:
    """Идемпотентно создаёт все фикстуры; возвращает пути летсплеев."""
    outs = []
    for n in range(1, FIXTURE_COUNT + 1):
        p = letsplay_path(n)
        if force or not p.exists():
            _make_letsplay(n, p)
        outs.append(p)
    if force or not music_path().exists():
        _make_music(music_path())
    for name in SFX_NAMES:
        p = sfx_path(name)
        if force or not p.exists():
            _make_sfx(name, p)
    return outs
