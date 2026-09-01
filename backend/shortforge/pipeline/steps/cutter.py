"""Cutter: 2-3 вертикальных сниппет-кандидата на блок из окон доноров.

Кандидаты 3-5 с (≤7 с от одного донора на клип, ADR-006), выбор окон по motion
score (media/vertical.py). rank=1 выбирается автоматически. Блоки без кандидатов
помечаются NEEDS_FOOTAGE. Rework идемпотентен: старые кандидаты блока удаляются.
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...media.ffutil import abs_path, job_dir, rel_path
from ...media.vertical import crop_vertical, snippet_windows
from ...models import Block, BlockStatus, ClipCandidate, Donor, VideoJob
from .. import flow
from .common import active_script, job_donors, key_words, script_blocks

CANDIDATES_PER_BLOCK = 3


def _donor_words(donor: Donor) -> set[str]:
    words: set[str] = set()
    for e in donor.transcript or []:
        words.update(w.lower() for w in str(e.get("s", "")).split())
    return words


async def run(session: AsyncSession, job: VideoJob, ctx: dict | None) -> str:
    script = await active_script(session, job)
    if script is None:
        raise RuntimeError("cutter: нет сценария")
    blocks = await script_blocks(session, script)
    donors = await job_donors(session, job)

    cand_dir = job_dir(job.batch_id, job.id, "candidates")
    windows_cache: dict[str, list[tuple[float, float, float]]] = {}
    needs_footage: list[Block] = []
    made = 0

    for block in blocks:
        # идемпотентность rework: пересоздаём кандидатов блока
        await session.execute(
            delete(ClipCandidate).where(ClipCandidate.block_id == block.id)
        )

        keys = key_words(block.search_keys)
        matched = [
            d for d in donors
            if abs_path(d.file_path).exists() and (_donor_words(d) & keys)
        ]
        if not matched:
            matched = [d for d in donors if abs_path(d.file_path).exists()]
        if not matched:
            block.status = BlockStatus.NEEDS_FOOTAGE
            needs_footage.append(block)
            continue

        rank = 0
        for i in range(CANDIDATES_PER_BLOCK):
            donor = matched[(block.ordinal - 1 + i) % len(matched)]
            src = abs_path(donor.file_path)
            if donor.id not in windows_cache:
                windows_cache[donor.id] = snippet_windows(
                    src, count=CANDIDATES_PER_BLOCK
                )
            windows = windows_cache[donor.id]
            if not windows:
                continue
            w_start, w_dur, score = windows[i % len(windows)]
            rank += 1
            out = cand_dir / f"b{block.ordinal:02d}_v{script.version}_r{rank}.mp4"
            crop_vertical(src, out, start=w_start, duration=w_dur)
            session.add(
                ClipCandidate(
                    block_id=block.id,
                    donor_id=donor.id,
                    rank=rank,
                    file_path=rel_path(out),
                    src_start=round(donor.window_start + w_start, 3),
                    duration=w_dur,
                    motion_score=score,
                    chosen=(rank == 1),
                )
            )
            made += 1

        if rank == 0:
            block.status = BlockStatus.NEEDS_FOOTAGE
            needs_footage.append(block)
        else:
            block.status = BlockStatus.OK

    await session.flush()
    await flow.after_cutter(session, job, ctx, needs_footage_blocks=needs_footage)
    return (
        f"candidates: {made} for {len(blocks)} blocks, "
        f"needs_footage={len(needs_footage)}"
    )


async def add_extra_candidates(
    session: AsyncSession, job: VideoJob, block_id: str,
    *, query: str | None = None, yt_url: str | None = None,
) -> str:
    """Дозаказ кандидатов для блока (задача extra_candidates)."""
    import re  # noqa: PLC0415

    from ...providers.factory import get_providers  # noqa: PLC0415

    block = (
        await session.execute(select(Block).where(Block.id == block_id))
    ).scalar_one_or_none()
    if block is None:
        raise RuntimeError(f"extra_candidates: block {block_id} не найден")

    providers = await get_providers(session)
    yt = providers.youtube

    if yt_url:
        m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{6,24})", yt_url)
        video_id = m.group(1) if m else yt_url[-11:]
        title, channel, duration = yt_url, "", 60.0
    else:
        q = query or (block.search_keys or [f"{job.game} gameplay"])[0]
        results = await yt.search(query=str(q), limit=3)
        if not results:
            raise RuntimeError("extra_candidates: поиск ничего не нашёл")
        found = results[0]
        video_id, title = found.yt_video_id, found.title
        channel, duration = found.channel, found.duration or 60.0

    w_start, w_end = 0.0, min(duration, 60.0)
    donors_dir = job_dir(job.batch_id, job.id, "donors")
    out = donors_dir / f"{video_id}_extra_{int(w_start)}_{int(w_end)}.mp4"
    await yt.download_window(
        yt_video_id=video_id, start=w_start, end=w_end, out_path=str(out)
    )
    donor = Donor(
        job_id=job.id, yt_video_id=video_id, yt_channel=channel, yt_title=title,
        window_start=w_start, window_end=w_end, file_path=rel_path(out),
        transcript=[], is_mock=yt.is_mock,
    )
    session.add(donor)
    await session.flush()

    max_rank = max(
        (c.rank for c in (
            await session.execute(
                select(ClipCandidate).where(ClipCandidate.block_id == block.id)
            )
        ).scalars()),
        default=0,
    )
    cand_dir = job_dir(job.batch_id, job.id, "candidates")
    note = f"extra: {query or yt_url}"[:256]
    added = 0
    for i, (s, d, score) in enumerate(snippet_windows(out, count=2)):
        rank = max_rank + i + 1
        cand_out = cand_dir / f"b{block.ordinal:02d}_extra_r{rank}.mp4"
        crop_vertical(out, cand_out, start=s, duration=d)
        session.add(
            ClipCandidate(
                block_id=block.id, donor_id=donor.id, rank=rank,
                file_path=rel_path(cand_out), src_start=round(w_start + s, 3),
                duration=d, motion_score=score, chosen=False, manual_note=note,
            )
        )
        added += 1
    if added and block.status == BlockStatus.NEEDS_FOOTAGE:
        block.status = BlockStatus.OK
    await session.flush()
    return f"extra_candidates: +{added} для блока {block.ordinal}"
