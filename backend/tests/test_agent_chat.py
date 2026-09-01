"""Чат-агент (mock): план предлагается и выполняется, правки применяются."""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from shortforge.models import (
    Batch, ChatMessage, ClipCandidate, JobStatus, Script, VideoFormat, VideoJob,
)
from shortforge.pipeline import worker as w

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def db_sessionmaker():
    from shortforge.db import get_sessionmaker

    return get_sessionmaker()


async def _drive_to_gate_rough(smaker, job_id):
    """Прогоняем джоб пайплайном до gate_rough (mock-провайдеры)."""
    await w.run_writer(None, job_id)
    await w.run_hunter(None, job_id)   # сам зачейнит cutter? нет: чейнит через ctx.redis; зовём руками
    await w.run_cutter(None, job_id)
    await w.run_voicer(None, job_id)
    await w.run_rough(None, job_id)


@pytest_asyncio.fixture()
async def rough_job(db_sessionmaker, fixture_videos):
    async with db_sessionmaker() as s:
        batch = Batch(title="t")
        s.add(batch)
        await s.flush()
        job = VideoJob(
            batch_id=batch.id, game="Steal a Brainrot",
            idea="secret trait", format=VideoFormat.A,
        )
        s.add(job)
        await s.commit()
        jid = job.id
    await _drive_to_gate_rough(db_sessionmaker, jid)
    return jid


async def _last_agent_msg(s, jid):
    return (
        await s.execute(
            select(ChatMessage)
            .where(ChatMessage.job_id == jid, ChatMessage.role == "agent")
            .order_by(ChatMessage.created_at.desc())
        )
    ).scalars().first()


async def test_plan_proposed_and_applied_replace_clip(db_sessionmaker, rough_job):
    jid = rough_job
    async with db_sessionmaker() as s:
        s.add(ChatMessage(job_id=jid, role="user", text="замени клип в блоке 2"))
        await s.commit()
    await w.agent_reply(None, jid, "m1")
    async with db_sessionmaker() as s:
        msg = await _last_agent_msg(s, jid)
        assert msg is not None and msg.extra.get("plan"), msg.text if msg else "no msg"
        assert msg.extra["plan"][0]["tool"] == "replace_clip"
        mid = msg.id
        before = {
            c.id: c.chosen
            for c in (await s.execute(select(ClipCandidate))).scalars()
        }
    await w.apply_plan(None, jid, mid)
    async with db_sessionmaker() as s:
        after = {
            c.id: c.chosen
            for c in (await s.execute(select(ClipCandidate))).scalars()
        }
        assert before != after, "выбор клипа не изменился"
        msg = await s.get(ChatMessage, mid)
        assert msg.extra.get("plan_status") == "executed"


async def test_retime_creates_new_script_version(db_sessionmaker, rough_job):
    jid = rough_job
    async with db_sessionmaker() as s:
        s.add(ChatMessage(job_id=jid, role="user", text="сделай короче до 20 сек"))
        await s.commit()
    await w.agent_reply(None, jid, "m2")
    async with db_sessionmaker() as s:
        msg = await _last_agent_msg(s, jid)
        assert msg.extra.get("plan") and msg.extra["plan"][0]["tool"] == "retime"
        mid = msg.id
        v_before = (
            await s.execute(select(Script.version).where(Script.job_id == jid).order_by(Script.version.desc()))
        ).scalars().first()
    await w.apply_plan(None, jid, mid)
    async with db_sessionmaker() as s:
        v_after = (
            await s.execute(select(Script.version).where(Script.job_id == jid).order_by(Script.version.desc()))
        ).scalars().first()
        assert v_after == v_before + 1


async def test_energetic_multi_step_plan(db_sessionmaker, rough_job):
    jid = rough_job
    async with db_sessionmaker() as s:
        s.add(ChatMessage(job_id=jid, role="user", text="сделай более энергичный видос"))
        await s.commit()
    await w.agent_reply(None, jid, "m3")
    async with db_sessionmaker() as s:
        msg = await _last_agent_msg(s, jid)
        tools = [p["tool"] for p in msg.extra.get("plan", [])]
        assert "add_sfx" in tools and "set_music" in tools


async def test_unknown_request_gets_text_reply(db_sessionmaker, rough_job):
    jid = rough_job
    async with db_sessionmaker() as s:
        s.add(ChatMessage(job_id=jid, role="user", text="что думаешь про погоду"))
        await s.commit()
    await w.agent_reply(None, jid, "m4")
    async with db_sessionmaker() as s:
        msg = await _last_agent_msg(s, jid)
        assert msg is not None and not msg.extra.get("plan") and msg.text
