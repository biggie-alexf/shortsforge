"""Запись событий в таблицу events (SSE-лента фронта)."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Event, VideoJob


async def emit(
    session: AsyncSession,
    type: str,
    *,
    job: VideoJob | None = None,
    job_id: str | None = None,
    batch_id: str | None = None,
    payload: dict | None = None,
) -> Event:
    """Создаёт Event; commit — на вызывающей стороне."""
    if job is not None:
        job_id = job_id or job.id
        batch_id = batch_id or job.batch_id
    ev = Event(type=type, job_id=job_id, batch_id=batch_id, payload=payload or {})
    session.add(ev)
    await session.flush()
    return ev


async def emit_status(session: AsyncSession, job: VideoJob) -> Event:
    return await emit(
        session, "job_status", job=job,
        payload={"status": job.status.value, "error": job.error},
    )
