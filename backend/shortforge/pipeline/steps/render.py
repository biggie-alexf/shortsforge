"""Rough/Master: сборка таймлайна из выбранных клипов (media/mix.py) + Render + changelog."""
from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...media import mix
from ...media.ffutil import abs_path, job_dir, rel_path
from ...media.qc import probe_qc
from ...media.subs import write_ass
from ...models import ClipCandidate, Render, VideoJob
from ...settings_store import get_setting
from .. import flow
from .common import active_script, latest_voice, script_blocks


async def _subs_dictionary(session: AsyncSession, job: VideoJob | None = None) -> dict[str, str]:
    raw = await get_setting(session, "subs_dictionary", "")
    data: dict[str, str] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = {str(k): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            pass
    # per-job overrides из чата-агента (overrides/subs_dictionary.json)
    if job is not None:
        p = job_dir(job.batch_id, job.id, "overrides") / "subs_dictionary.json"
        if p.exists():
            try:
                ov = json.loads(p.read_text())
                if isinstance(ov, dict):
                    data.update({
                        str(k): str(v) for k, v in ov.items() if k != "__notes__"
                    })
            except json.JSONDecodeError:
                pass
    return data


def _music_volume(job: VideoJob) -> float | None:
    p = job_dir(job.batch_id, job.id, "overrides") / "music.json"
    if p.exists():
        try:
            v = json.loads(p.read_text()).get("volume")
            return float(v) if v is not None else None
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    return None


async def _gather(session: AsyncSession, job: VideoJob):
    script = await active_script(session, job)
    if script is None:
        raise RuntimeError("render: нет сценария")
    blocks = await script_blocks(session, script)
    voice = await latest_voice(session, job)
    if voice is None:
        raise RuntimeError("render: нет озвучки (voicer не отработал)")

    mix_blocks: list[mix.MixBlock] = []
    for block in blocks:
        cand = (
            await session.execute(
                select(ClipCandidate)
                .where(ClipCandidate.block_id == block.id, ClipCandidate.chosen)
                .order_by(ClipCandidate.rank)
                .limit(1)
            )
        ).scalar_one_or_none()
        if cand is None:
            raise RuntimeError(
                f"render: у блока {block.ordinal} ({block.role}) нет выбранного клипа"
            )
        if block.t_start is None or block.t_end is None:
            raise RuntimeError(f"render: блок {block.ordinal} без таймингов voicer-а")
        fx = block.fx or []
        mix_blocks.append(
            mix.MixBlock(
                clip=abs_path(cand.file_path),
                duration=max(0.2, float(block.t_end) - float(block.t_start)),
                zoom=any(f.get("t") == "zoom" for f in fx),
                sfx=[f.get("name", "") for f in fx if f.get("t") == "sfx"],
            )
        )
    return script, blocks, voice, mix_blocks


async def _next_version(session: AsyncSession, job: VideoJob, kind: str) -> int:
    cur = (
        await session.execute(
            select(func.max(Render.version)).where(
                Render.job_id == job.id, Render.kind == kind
            )
        )
    ).scalar()
    return (cur or 0) + 1


async def _make_render(
    session: AsyncSession, job: VideoJob, ctx: dict | None, *, kind: str
) -> Render:
    script, blocks, voice, mix_blocks = await _gather(session, job)
    version = await _next_version(session, job, kind)

    renders_dir = job_dir(job.batch_id, job.id, "renders")
    workdir = renders_dir / f"_work_{kind}_v{version}"
    ass_file = renders_dir / f"subs_{kind}_v{version}.ass"
    out_mp4 = renders_dir / f"{kind}_v{version}.mp4"
    preview = renders_dir / f"{kind}_v{version}.jpg"

    write_ass(ass_file, voice.words, dictionary=await _subs_dictionary(session, job))
    voice_wav = abs_path(voice.wav_path)

    if kind == "rough":
        mix.render_rough(
            blocks=mix_blocks, voice_wav=voice_wav, ass_file=ass_file,
            out_mp4=out_mp4, workdir=workdir, fps=30,
        )
    else:
        mix.render_master(
            blocks=mix_blocks, voice_wav=voice_wav, ass_file=ass_file,
            title=script.title, out_mp4=out_mp4, workdir=workdir, fps=60,
            music_volume=_music_volume(job),
        )
    mix.make_preview(out_mp4, preview)

    changelog = (
        f"{kind} v{version} из сценария v{script.version} "
        f"({len(mix_blocks)} блоков, голос {voice.duration:.1f}s)"
    )
    if version > 1:
        changelog += " — rework, предыдущая версия сохранена"

    render = Render(
        job_id=job.id, kind=kind, version=version,
        file_path=rel_path(out_mp4), preview_path=rel_path(preview),
        changelog=changelog, qc=probe_qc(out_mp4),
    )
    session.add(render)
    await session.flush()
    return render


async def run_rough(session: AsyncSession, job: VideoJob, ctx: dict | None) -> str:
    render = await _make_render(session, job, ctx, kind="rough")
    await flow.after_rough(session, job, ctx, render)
    return f"rough v{render.version}: {render.file_path}, qc={render.qc}"


async def run_master(session: AsyncSession, job: VideoJob, ctx: dict | None) -> str:
    render = await _make_render(session, job, ctx, kind="master")
    await flow.after_master(session, job, ctx, render)
    return f"master v{render.version}: {render.file_path}, qc={render.qc}"
