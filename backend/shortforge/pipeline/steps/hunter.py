"""Hunter: поиск доноров по search_keys блоков и скачивание окон (ADR-006).

Окно ±30 с вокруг найденного в транскрипте момента. Идемпотентность rework:
донор с тем же (yt_video_id, окно) в рамках job-а не скачивается повторно.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ...media.ffutil import job_dir, rel_path
from ...models import Donor, VideoJob
from ...providers.factory import get_providers
from .. import flow
from .common import active_script, job_donors, key_words, script_blocks

WINDOW_HALF = 30.0  # ±30 с вокруг момента (ADR-006)


def _find_moment(transcript: list[dict], keys: set[str], fallback: float) -> float:
    for entry in transcript:
        words = {w.lower() for w in str(entry.get("s", "")).split()}
        if words & keys:
            return float(entry.get("t", 0.0))
    return fallback


async def run(session: AsyncSession, job: VideoJob, ctx: dict | None) -> str:
    providers = await get_providers(session)
    yt = providers.youtube

    script = await active_script(session, job)
    if script is None:
        raise RuntimeError("hunter: нет сценария (writer не отработал)")
    blocks = await script_blocks(session, script)

    existing = {
        (d.yt_video_id, round(d.window_start, 1), round(d.window_end, 1)): d
        for d in await job_donors(session, job)
    }
    donors_dir = job_dir(job.batch_id, job.id, "donors")
    downloaded = 0
    reused = 0

    for block in blocks:
        query = (block.search_keys or [f"{job.game} gameplay"])[0]
        results = await yt.search(query=str(query), limit=3)
        if not results:
            continue
        # разные доноры на разные блоки (несколько доноров на видео, ADR-006)
        found = results[(block.ordinal - 1) % len(results)]
        transcript = found.transcript or await yt.get_transcript(
            yt_video_id=found.yt_video_id
        )
        duration = found.duration or 60.0
        moment = _find_moment(transcript, key_words(block.search_keys), duration / 2)
        w_start = max(0.0, round(moment - WINDOW_HALF, 1))
        w_end = min(duration, round(moment + WINDOW_HALF, 1))
        if w_end - w_start < 4.0:
            w_start, w_end = 0.0, min(duration, 2 * WINDOW_HALF)

        cache_key = (found.yt_video_id, round(w_start, 1), round(w_end, 1))
        if cache_key in existing:
            reused += 1
            continue

        out = donors_dir / f"{found.yt_video_id}_{int(w_start)}_{int(w_end)}.mp4"
        await yt.download_window(
            yt_video_id=found.yt_video_id, start=w_start, end=w_end, out_path=str(out)
        )
        window_transcript = [
            e for e in transcript if w_start <= float(e.get("t", 0)) <= w_end
        ]
        donor = Donor(
            job_id=job.id,
            yt_video_id=found.yt_video_id,
            yt_channel=found.channel,
            yt_title=found.title,
            window_start=w_start,
            window_end=w_end,
            file_path=rel_path(out),
            transcript=window_transcript,
            is_mock=yt.is_mock,
        )
        session.add(donor)
        existing[cache_key] = donor
        downloaded += 1

    await session.flush()
    await flow.after_hunter(session, job, ctx)
    return f"donors: {downloaded} downloaded, {reused} reused, blocks={len(blocks)}"
