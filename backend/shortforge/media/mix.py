"""Сборка rough и master.

rough:  concat выбранных клипов (обрезка/луп под t_start/t_end блоков) + voice.wav + ASS-сабы.
master: то же + музыка (sidechaincompress под голос), SFX по fx-меткам, плашка-заголовок
        (drawtext, первые 2.5 с), zoompan на блоках с fx zoom, loudnorm -14 LUFS,
        1080x1920 60fps, превью-jpg первого кадра.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .ffutil import run_ffmpeg
from .fixtures import ensure_fixtures, music_path, sfx_path

FONT_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"


@dataclass
class MixBlock:
    """Один блок таймлайна: клип + целевая длительность + эффекты."""
    clip: Path                       # вертикальный сниппет (1080x1920, без звука)
    duration: float                  # t_end - t_start блока
    zoom: bool = False               # fx: {"t":"zoom"}
    sfx: list[str] = field(default_factory=list)  # fx: {"t":"sfx","name":...}


def _segment(block: MixBlock, out: Path, *, fps: int) -> Path:
    """Клип, залупленный/обрезанный под длительность блока, единый формат."""
    vf = "scale=1080:1920,setsar=1"
    if block.zoom:
        vf += (
            f",zoompan=z='min(1.10,1+0.0018*in)':x='(iw-iw/zoom)/2':"
            f"y='(ih-ih/zoom)/2':d=1:s=1080x1920:fps={fps}"
        )
    run_ffmpeg(
        [
            "-stream_loop", "8", "-i", str(block.clip),
            "-t", f"{max(0.2, block.duration):.3f}",
            "-vf", vf, "-r", str(fps), "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", str(out),
        ]
    )
    return out


def _concat(segments: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p}'\n" for p in segments), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    return out


def _build_timeline(blocks: list[MixBlock], workdir: Path, *, fps: int, tag: str) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    segs = [
        _segment(b, workdir / f"{tag}_seg_{i:02d}.mp4", fps=fps)
        for i, b in enumerate(blocks)
    ]
    return _concat(segs, workdir / f"{tag}_timeline.mp4")


def _drawtext_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:")
        .replace("%", "\\%").replace(",", "\\,")
    )


def _ass_escape(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace(",", "\\,")


def render_rough(
    *, blocks: list[MixBlock], voice_wav: Path, ass_file: Path,
    out_mp4: Path, workdir: Path, fps: int = 30,
) -> Path:
    """Черновик: таймлайн + голос + вшитые сабы."""
    timeline = _build_timeline(blocks, workdir, fps=fps, tag="rough")
    total = sum(max(0.2, b.duration) for b in blocks)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i", str(timeline), "-i", str(voice_wav),
            "-vf", f"ass={_ass_escape(ass_file)}",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-t", f"{total:.3f}", str(out_mp4),
        ]
    )
    return out_mp4


def render_master(
    *, blocks: list[MixBlock], voice_wav: Path, ass_file: Path, title: str,
    out_mp4: Path, workdir: Path, fps: int = 60,
) -> Path:
    """Мастер: музыка с дакингом, SFX по меткам, плашка, zoompan, loudnorm -14 LUFS."""
    ensure_fixtures()
    timeline = _build_timeline(blocks, workdir, fps=fps, tag="master")
    total = sum(max(0.2, b.duration) for b in blocks)

    # SFX: (файл, момент начала блока)
    sfx_events: list[tuple[Path, float]] = []
    t = 0.0
    for b in blocks:
        for name in b.sfx:
            p = sfx_path(name)
            if p.exists():
                sfx_events.append((p, t))
        t += max(0.2, b.duration)

    inputs: list[str] = [
        "-i", str(timeline),
        "-i", str(voice_wav),
        "-stream_loop", "-1", "-i", str(music_path()),
    ]
    for p, _ in sfx_events:
        inputs += ["-i", str(p)]

    vf = (
        f"ass={_ass_escape(ass_file)},"
        f"drawtext=fontfile={FONT_BOLD}:text='{_drawtext_escape(title.upper())}':"
        f"enable='lt(t\\,2.5)':x=(w-text_w)/2:y=300:fontsize=58:fontcolor=white:"
        f"box=1:boxcolor=black@0.6:boxborderw=26"
    )
    fc = [
        f"[0:v]{vf}[v]",
        "[1:a]aformat=sample_rates=44100:channel_layouts=mono,asplit=2[vo1][vo2]",
        f"[2:a]atrim=0:{total:.3f},aformat=sample_rates=44100:channel_layouts=mono,"
        "volume=0.30[mus]",
        "[mus][vo2]sidechaincompress=threshold=0.02:ratio=12:attack=5:release=300[duck]",
    ]
    mix_ins = ["[vo1]", "[duck]"]
    for i, (_, at) in enumerate(sfx_events):
        ms = int(at * 1000)
        fc.append(
            f"[{3 + i}:a]aformat=sample_rates=44100:channel_layouts=mono,"
            f"adelay={ms}|{ms}[sfx{i}]"
        )
        mix_ins.append(f"[sfx{i}]")
    fc.append(
        f"{''.join(mix_ins)}amix=inputs={len(mix_ins)}:duration=first:normalize=0,"
        "loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    )

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            *inputs,
            "-filter_complex", ";".join(fc),
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "160k",
            "-t", f"{total:.3f}", str(out_mp4),
        ]
    )
    _trim_to_target_lufs(out_mp4, workdir)
    return out_mp4


def _trim_to_target_lufs(out_mp4: Path, workdir: Path, target: float = -14.0) -> None:
    """Второй проход громкости: one-pass loudnorm на коротком контенте недобирает
    до цели, поэтому меряем integrated LUFS и докручиваем ровным гейном
    (видео копируется, пережимается только аудио)."""
    from .qc import measure_lufs  # noqa: PLC0415 (qc не импортирует mix — цикла нет)

    lufs = measure_lufs(out_mp4)
    if lufs is None or abs(lufs - target) <= 0.5:
        return
    gain = target - lufs
    fixed = workdir / f"lufs_fix_{out_mp4.name}"
    run_ffmpeg(
        [
            "-i", str(out_mp4), "-map", "0:v", "-map", "0:a",
            "-c:v", "copy", "-af", f"volume={gain:.2f}dB,alimiter=limit=0.891",
            "-c:a", "aac", "-b:a", "160k", str(fixed),
        ]
    )
    fixed.replace(out_mp4)


def make_preview(mp4: Path, jpg: Path) -> Path:
    jpg.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(mp4), "-frames:v", "1", "-q:v", "3", str(jpg)])
    return jpg
