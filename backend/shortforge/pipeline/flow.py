"""Оркестрация переходов статусной машины (api-spec.md, низ файла).

Каждый шаг в конце сам решает следующий переход:
- writer  -> gate_script (Gate(script)=open, Event gate_open)
- hunter  -> cutting     (+ enqueue run_cutter)
- cutter  -> gate_clips  (+ needs_footage Event по блокам без кандидатов)
- voicer  -> rough_render (+ enqueue run_rough)
- rough   -> gate_rough
- master  -> gate_master (+ job.current_version = version)
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Block, Gate, GateStatus, GateType, JobStatus, Render, VideoJob
from .events import emit, emit_status

GATE_ORDINALS = {
    GateType.SCRIPT: 1,
    GateType.CLIPS: 2,
    GateType.ROUGH: 3,
    GateType.MASTER: 4,
}

GATE_STATUS = {
    GateType.SCRIPT: JobStatus.GATE_SCRIPT,
    GateType.CLIPS: JobStatus.GATE_CLIPS,
    GateType.ROUGH: JobStatus.GATE_ROUGH,
    GateType.MASTER: JobStatus.GATE_MASTER,
}


async def ensure_gates(session: AsyncSession, job: VideoJob) -> dict[GateType, Gate]:
    """Идемпотентно создаёт 4 гейта job-а."""
    rows = (
        await session.execute(select(Gate).where(Gate.job_id == job.id))
    ).scalars().all()
    have = {g.type: g for g in rows}
    for gtype, ordinal in GATE_ORDINALS.items():
        if gtype not in have:
            g = Gate(job_id=job.id, type=gtype, ordinal=ordinal)
            session.add(g)
            have[gtype] = g
    await session.flush()
    return have


async def set_status(session: AsyncSession, job: VideoJob, status: JobStatus) -> None:
    job.status = status
    await emit_status(session, job)


async def open_gate(session: AsyncSession, job: VideoJob, gtype: GateType) -> Gate:
    gates = await ensure_gates(session, job)
    gate = gates[gtype]
    gate.status = GateStatus.OPEN
    gate.approved_by = None
    gate.approved_at = None
    await set_status(session, job, GATE_STATUS[gtype])
    await emit(
        session, "gate_open", job=job,
        payload={"gate": gtype.value, "status": job.status.value},
    )
    return gate


async def _enqueue(ctx: dict | None, task: str, *args) -> bool:
    """В arq-контексте ставит следующую задачу; в тестах (ctx без redis) — нет."""
    redis = (ctx or {}).get("redis")
    if redis is None:
        return False
    await redis.enqueue_job(task, *args)
    return True


# ------------------------------------------------------------ переходы шагов

async def after_writer(session: AsyncSession, job: VideoJob, ctx: dict | None) -> None:
    await open_gate(session, job, GateType.SCRIPT)


async def after_hunter(session: AsyncSession, job: VideoJob, ctx: dict | None) -> None:
    await set_status(session, job, JobStatus.CUTTING)
    await _enqueue(ctx, "run_cutter", job.id)


async def after_cutter(
    session: AsyncSession, job: VideoJob, ctx: dict | None,
    *, needs_footage_blocks: list[Block] | None = None,
) -> None:
    for b in needs_footage_blocks or []:
        await emit(
            session, "needs_footage", job=job,
            payload={"block_id": b.id, "ordinal": b.ordinal, "role": b.role},
        )
    await open_gate(session, job, GateType.CLIPS)


async def after_voicer(session: AsyncSession, job: VideoJob, ctx: dict | None) -> None:
    await set_status(session, job, JobStatus.ROUGH_RENDER)
    await _enqueue(ctx, "run_rough", job.id)


async def after_rough(
    session: AsyncSession, job: VideoJob, ctx: dict | None, render: Render
) -> None:
    await emit(
        session, "render_ready", job=job,
        payload={"kind": "rough", "render_id": render.id, "version": render.version},
    )
    await open_gate(session, job, GateType.ROUGH)


async def after_master(
    session: AsyncSession, job: VideoJob, ctx: dict | None, render: Render
) -> None:
    job.current_version = render.version
    await emit(
        session, "render_ready", job=job,
        payload={"kind": "master", "render_id": render.id, "version": render.version},
    )
    await open_gate(session, job, GateType.MASTER)


async def fail_job(session: AsyncSession, job: VideoJob, error: str) -> None:
    job.status = JobStatus.FAILED
    job.error = error[:4000]
    await emit_status(session, job)
    await emit(session, "error", job=job, payload={"error": job.error})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
