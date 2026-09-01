"""Общие выборки для шагов пайплайна (async, без ленивой загрузки отношений)."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Block, Donor, Script, VideoJob, VoiceTrack


async def active_script(session: AsyncSession, job: VideoJob) -> Script | None:
    """Активная версия сценария = max(version)."""
    return (
        await session.execute(
            select(Script)
            .where(Script.job_id == job.id)
            .order_by(Script.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def script_blocks(session: AsyncSession, script: Script) -> list[Block]:
    return list(
        (
            await session.execute(
                select(Block)
                .where(Block.script_id == script.id)
                .order_by(Block.ordinal)
            )
        ).scalars()
    )


async def job_donors(session: AsyncSession, job: VideoJob) -> list[Donor]:
    return list(
        (
            await session.execute(
                select(Donor).where(Donor.job_id == job.id).order_by(Donor.created_at)
            )
        ).scalars()
    )


async def latest_voice(session: AsyncSession, job: VideoJob) -> VoiceTrack | None:
    return (
        await session.execute(
            select(VoiceTrack)
            .where(VoiceTrack.job_id == job.id)
            .order_by(VoiceTrack.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def key_words(keys: list) -> set[str]:
    """Множество слов из search_keys блока (lower)."""
    out: set[str] = set()
    for k in keys or []:
        out.update(w.lower() for w in re.findall(r"\w+", str(k)))
    return out


def words_count(text: str) -> int:
    return len(re.findall(r"\S+", text))
