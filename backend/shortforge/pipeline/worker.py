"""arq-воркер: WorkerSettings + задачи пайплайна (api-spec.md, низ файла).

Каждая задача: своя async-сессия БД, StepRun (старт/финиш/ошибка); при исключении
job.status=failed, job.error, Event(type=error). agent_reply/apply_plan — заглушки
(реализует интегратор, ADR-005).
"""
from __future__ import annotations

import logging
import os
import traceback

from arq.connections import RedisSettings

from ..db import get_sessionmaker
from ..models import ChatMessage, JobStatus, StepName, StepRun, VideoJob
from . import flow
from .events import emit
from .steps import cutter, hunter, render, voicer, writer

log = logging.getLogger("shortforge.worker")

STEP_STATUS = {
    StepName.WRITER: JobStatus.SCRIPTING,
    StepName.HUNTER: JobStatus.HUNTING,
    StepName.CUTTER: JobStatus.CUTTING,
    StepName.VOICER: JobStatus.VOICING,
    StepName.ROUGH_MIXER: JobStatus.ROUGH_RENDER,
    StepName.MASTER_MIXER: JobStatus.MASTER_RENDER,
}


async def _run_step(ctx: dict | None, job_id: str, step: StepName, fn) -> str:
    """Общий каркас шага: StepRun, статус, транзакции, обработка ошибок."""
    async with get_sessionmaker()() as session:
        job = await session.get(VideoJob, job_id)
        if job is None:
            log.error("%s: job %s not found", step.value, job_id)
            return f"job {job_id} not found"

        run = StepRun(job_id=job_id, step=step)
        session.add(run)
        await flow.set_status(session, job, STEP_STATUS[step])
        await session.commit()
        run_id = run.id

        try:
            detail = await fn(session, job, ctx)
            run.ok = True
            run.detail = detail or ""
            run.finished_at = flow.utcnow()
            await session.commit()
            log.info("%s(%s): %s", step.value, job_id, detail)
            return detail
        except Exception as e:  # noqa: BLE001
            log.exception("%s(%s) failed", step.value, job_id)
            await session.rollback()
            err = f"{type(e).__name__}: {e}"
            run2 = await session.get(StepRun, run_id)
            if run2 is not None:
                run2.ok = False
                run2.detail = f"{err}\n{traceback.format_exc()[-1500:]}"
                run2.finished_at = flow.utcnow()
            job2 = await session.get(VideoJob, job_id)
            if job2 is not None:
                await flow.fail_job(session, job2, err)
            await session.commit()
            return f"FAILED: {err}"


# ------------------------------------------------------------------ задачи

async def run_writer(ctx: dict | None, job_id: str) -> str:
    return await _run_step(ctx, job_id, StepName.WRITER, writer.run)


async def run_hunter(ctx: dict | None, job_id: str) -> str:
    return await _run_step(ctx, job_id, StepName.HUNTER, hunter.run)


async def run_cutter(ctx: dict | None, job_id: str) -> str:
    return await _run_step(ctx, job_id, StepName.CUTTER, cutter.run)


async def run_voicer(ctx: dict | None, job_id: str) -> str:
    return await _run_step(ctx, job_id, StepName.VOICER, voicer.run)


async def run_rough(ctx: dict | None, job_id: str) -> str:
    return await _run_step(ctx, job_id, StepName.ROUGH_MIXER, render.run_rough)


async def run_master(ctx: dict | None, job_id: str) -> str:
    return await _run_step(ctx, job_id, StepName.MASTER_MIXER, render.run_master)


async def agent_reply(ctx: dict | None, job_id: str, message_id: str) -> str:
    """ЗАГЛУШКА: чат-агента подключает интегратор (ADR-005)."""
    async with get_sessionmaker()() as session:
        job = await session.get(VideoJob, job_id)
        msg = ChatMessage(
            job_id=job_id, role="agent",
            text="агент ещё не подключён",
            extra={"stub": True, "reply_to": message_id},
        )
        session.add(msg)
        await session.flush()
        await emit(
            session, "chat", job=job, job_id=job_id,
            payload={"message_id": msg.id, "role": "agent", "stub": True},
        )
        await session.commit()
        return f"agent_reply stub -> {msg.id}"


async def apply_plan(ctx: dict | None, job_id: str, message_id: str) -> str:
    """ЗАГЛУШКА: выполнение плана агента подключает интегратор (ADR-005)."""
    async with get_sessionmaker()() as session:
        job = await session.get(VideoJob, job_id)
        msg = ChatMessage(
            job_id=job_id, role="agent",
            text="агент ещё не подключён",
            extra={"stub": True, "plan_message_id": message_id},
        )
        session.add(msg)
        await session.flush()
        await emit(
            session, "chat", job=job, job_id=job_id,
            payload={"message_id": msg.id, "role": "agent", "stub": True},
        )
        await session.commit()
        return f"apply_plan stub -> {msg.id}"


async def extra_candidates(
    ctx: dict | None, job_id: str, block_id: str,
    query: str | None = None, yt_url: str | None = None,
) -> str:
    """Дозаказ кандидатов для блока (кнопка на гейте G2)."""
    async with get_sessionmaker()() as session:
        job = await session.get(VideoJob, job_id)
        if job is None:
            return f"job {job_id} not found"
        try:
            detail = await cutter.add_extra_candidates(
                session, job, block_id, query=query, yt_url=yt_url
            )
            await emit(
                session, "chat", job=job,
                payload={"text": detail, "block_id": block_id},
            )
            await session.commit()
            return detail
        except Exception as e:  # noqa: BLE001
            log.exception("extra_candidates(%s) failed", job_id)
            await session.rollback()
            await emit(
                session, "error", job_id=job_id, batch_id=job.batch_id,
                payload={"error": f"extra_candidates: {e}", "block_id": block_id},
            )
            await session.commit()
            return f"FAILED: {e}"


# ------------------------------------------------------------------ arq

def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(
        os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    )


class WorkerSettings:
    functions = [
        run_writer, run_hunter, run_cutter, run_voicer, run_rough, run_master,
        agent_reply, apply_plan, extra_candidates,
    ]
    redis_settings = _redis_settings()
    max_jobs = 2
    job_timeout = 1800
    keep_result = 3600
