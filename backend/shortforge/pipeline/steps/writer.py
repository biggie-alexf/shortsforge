"""Writer: два прохода LLM (write_script + punch_up) -> Script version=next + Block-и.

Rework идемпотентен: каждый запуск пишет НОВУЮ версию сценария, старые не трогаются.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Block, Script, VideoJob
from ...providers.factory import get_providers
from .. import flow

BIBLE_CONTEXT = (
    "Format A 'secret/breakdown': 18-45s, 55-120 EN words, 2.6-3.0 wps, blocks "
    "hook(<=12w) -> setup -> evidence x2-3 -> cta (~13s, '5 seconds to like or...') "
    "-> twist -> loop (open ending, cut mid-action). Shot change every 3-5s. "
    "Format B 'meme monologue': 25-35s, 70-100 words, paradox -> weird rule -> absurd "
    "consequence -> everyday conclusion, cut mid-word; neutral obby parkour b-roll. "
    "Titles: caps on the hot word, '..', <=55 chars (A); all lowercase tired tone (B)."
)


async def run(session: AsyncSession, job: VideoJob, ctx: dict | None) -> str:
    providers = await get_providers(session)
    llm = providers.llm

    draft = await llm.write_script(
        game=job.game, idea=job.idea, fmt=job.format.value, context=BIBLE_CONTEXT
    )
    draft = await llm.punch_up(draft, game=job.game, fmt=job.format.value)

    next_version = (
        (
            await session.execute(
                select(func.max(Script.version)).where(Script.job_id == job.id)
            )
        ).scalar()
        or 0
    ) + 1

    script = Script(
        job_id=job.id,
        version=next_version,
        title=draft.title,
        description=draft.description,
        hook_pattern=draft.hook_pattern,
        notes=(
            f"writer: write_script + punch_up, provider="
            f"{'mock' if llm.is_mock else 'real'}, game={job.game!r}, idea={job.idea!r}"
        ),
    )
    session.add(script)
    await session.flush()

    for b in draft.blocks:
        session.add(
            Block(
                script_id=script.id,
                ordinal=int(b.get("ordinal", 0)),
                role=str(b.get("role", "")),
                text_en=str(b.get("text_en", "")),
                frame_desc=str(b.get("frame_desc", "")),
                search_keys=list(b.get("search_keys", [])),
                fx=list(b.get("fx", [])),
            )
        )
    await session.flush()
    await flow.after_writer(session, job, ctx)
    return f"script v{next_version}: {len(draft.blocks)} blocks, title={draft.title!r}"
