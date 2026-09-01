"""ShortForge API — FastAPI-приложение. Контракт: docs/api-spec.md (не менять).

Эндпоинты: auth, batches/jobs, gates, blocks, chat, media, settings, users, events.
Очередь: arq по именам задач (см. api/queue.py), воркер — в pipeline/ (другой агент).
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import settings_store
from ..db import get_session, get_sessionmaker
from ..models import (
    Batch,
    Block,
    BlockStatus,
    ChatMessage,
    ClipCandidate,
    Donor,
    Event,
    Gate,
    GateStatus,
    GateType,
    JobStatus,
    Render,
    Script,
    StepName,
    StepRun,
    User,
    VideoFormat,
    VideoJob,
    VoiceTrack,
)
from ..security import (
    SESSION_COOKIE,
    SESSION_TTL,
    current_user,
    hash_password,
    make_session_cookie,
    read_session_cookie,
    verify_password,
)
from . import queue

# ---------------------------------------------------------------- helpers

GATE_ORDER = [GateType.SCRIPT, GateType.CLIPS, GateType.ROUGH, GateType.MASTER]

# гейт -> (статус, на котором он открыт; следующий статус; задача для очереди)
GATE_TRANSITIONS: dict[GateType, tuple[JobStatus, JobStatus, str | None]] = {
    GateType.SCRIPT: (JobStatus.GATE_SCRIPT, JobStatus.HUNTING, "run_hunter"),
    GateType.CLIPS: (JobStatus.GATE_CLIPS, JobStatus.VOICING, "run_voicer"),
    GateType.ROUGH: (JobStatus.GATE_ROUGH, JobStatus.MASTER_RENDER, "run_master"),
    GateType.MASTER: (JobStatus.GATE_MASTER, JobStatus.DONE, None),
}

# retry: рабочий статус -> задача
STATUS_TASK: dict[JobStatus, str] = {
    JobStatus.QUEUED: "run_writer",
    JobStatus.SCRIPTING: "run_writer",
    JobStatus.HUNTING: "run_hunter",
    JobStatus.CUTTING: "run_cutter",
    JobStatus.VOICING: "run_voicer",
    JobStatus.ROUGH_RENDER: "run_rough",
    JobStatus.MASTER_RENDER: "run_master",
}

# retry после failed: последний шаг -> (статус, задача)
STEP_RESTART: dict[StepName, tuple[JobStatus, str]] = {
    StepName.WRITER: (JobStatus.SCRIPTING, "run_writer"),
    StepName.HUNTER: (JobStatus.HUNTING, "run_hunter"),
    StepName.CUTTER: (JobStatus.CUTTING, "run_cutter"),
    StepName.VOICER: (JobStatus.VOICING, "run_voicer"),
    StepName.ROUGH_MIXER: (JobStatus.ROUGH_RENDER, "run_rough"),
    StepName.MASTER_MIXER: (JobStatus.MASTER_RENDER, "run_master"),
}


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def media_url(path: str | None) -> str | None:
    if not path:
        return None
    return "/media/" + path.lstrip("/")


async def write_event(
    session: AsyncSession,
    type: str,
    job_id: str | None = None,
    batch_id: str | None = None,
    payload: dict | None = None,
) -> None:
    session.add(
        Event(type=type, job_id=job_id, batch_id=batch_id, payload=payload or {})
    )
    await session.flush()


def job_summary(job: VideoJob, open_gate: GateType | None) -> dict:
    return {
        "id": job.id,
        "game": job.game,
        "idea": job.idea,
        "format": job.format.value,
        "status": job.status.value,
        "current_version": job.current_version,
        "open_gate": open_gate.value if open_gate else None,
        "error": job.error,
    }


def find_open_gate(gates: list[Gate]) -> GateType | None:
    for g in gates:
        if g.status == GateStatus.OPEN:
            return g.type
    return None


async def get_job_or_404(session: AsyncSession, job_id: str) -> VideoJob:
    job = (
        await session.execute(select(VideoJob).where(VideoJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


# ---------------------------------------------------------------- schemas (входные)


class LoginIn(BaseModel):
    login: str
    password: str


class JobIn(BaseModel):
    game: str
    idea: str
    format: str = "A"


class BatchIn(BaseModel):
    title: str = ""
    jobs: list[JobIn]


class ChooseIn(BaseModel):
    candidate_id: str


class CandidatesIn(BaseModel):
    query: str | None = None
    yt_url: str | None = None


class ChatIn(BaseModel):
    text: str


class SettingIn(BaseModel):
    value: str


class UserIn(BaseModel):
    login: str
    password: str


# ---------------------------------------------------------------- app


def create_app() -> FastAPI:
    app = FastAPI(title="ShortForge API")

    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_exc_handler(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": str(exc)}, status_code=422)

    @app.middleware("http")
    async def media_session_guard(request: Request, call_next):
        # /media/* — статика под сессией: 401 без валидной куки
        if request.url.path.startswith("/media"):
            uid = read_session_cookie(request.cookies.get(SESSION_COOKIE))
            if uid is None:
                return JSONResponse({"error": "not authenticated"}, status_code=401)
        return await call_next(request)

    @app.on_event("shutdown")
    async def _shutdown():
        await queue.close_pool()

    # ---------------------------------------------------------- auth

    @app.post("/api/auth/login")
    async def login(
        body: LoginIn, response: Response, session: AsyncSession = Depends(get_session)
    ):
        user = (
            await session.execute(select(User).where(User.login == body.login))
        ).scalar_one_or_none()
        if user is None or not verify_password(body.password, user.pw_hash):
            raise HTTPException(status_code=401, detail="invalid credentials")
        response.set_cookie(
            SESSION_COOKIE,
            make_session_cookie(user.id),
            max_age=SESSION_TTL,
            httponly=True,
            samesite="lax",
        )
        return {"user": {"id": user.id, "login": user.login}}

    @app.post("/api/auth/logout")
    async def logout(response: Response):
        response.delete_cookie(SESSION_COOKIE)
        return {}

    @app.get("/api/auth/me")
    async def me(user: User = Depends(current_user)):
        return {"user": {"id": user.id, "login": user.login}}

    # ---------------------------------------------------------- batches / jobs

    @app.get("/api/batches")
    async def list_batches(
        user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
    ):
        batches = (
            (
                await session.execute(
                    select(Batch)
                    .options(selectinload(Batch.jobs).selectinload(VideoJob.gates))
                    .order_by(desc(Batch.created_at))
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": b.id,
                "title": b.title,
                "created_at": iso(b.created_at),
                "jobs": [job_summary(j, find_open_gate(j.gates)) for j in b.jobs],
            }
            for b in batches
        ]

    @app.post("/api/batches")
    async def create_batch(
        body: BatchIn,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        if not body.jobs:
            raise HTTPException(status_code=422, detail="jobs must not be empty")
        try:
            formats = [VideoFormat(j.format) for j in body.jobs]
        except ValueError:
            raise HTTPException(status_code=422, detail="unknown format")
        batch = Batch(title=body.title, created_by=user.id)
        session.add(batch)
        await session.flush()
        job_ids: list[str] = []
        for j, fmt in zip(body.jobs, formats):
            job = VideoJob(
                batch_id=batch.id,
                game=j.game,
                idea=j.idea,
                format=fmt,
                status=JobStatus.QUEUED,
            )
            session.add(job)
            await session.flush()
            for ordinal, gtype in enumerate(GATE_ORDER, start=1):
                session.add(
                    Gate(
                        job_id=job.id,
                        type=gtype,
                        ordinal=ordinal,
                        status=GateStatus.PENDING,
                    )
                )
            await write_event(
                session,
                "job_status",
                job_id=job.id,
                batch_id=batch.id,
                payload={"status": JobStatus.QUEUED.value, "game": job.game},
            )
            job_ids.append(job.id)
        await session.commit()
        for jid in job_ids:
            await queue.enqueue("run_writer", jid)
        return {"id": batch.id}

    @app.get("/api/jobs/{job_id}")
    async def job_detail(
        job_id: str,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        job = await get_job_or_404(session, job_id)
        gates = (
            (
                await session.execute(
                    select(Gate).where(Gate.job_id == job.id).order_by(Gate.ordinal)
                )
            )
            .scalars()
            .all()
        )

        # активный сценарий = max(version)
        script = (
            await session.execute(
                select(Script)
                .where(Script.job_id == job.id)
                .order_by(desc(Script.version))
                .limit(1)
                .options(
                    selectinload(Script.blocks).selectinload(Block.candidates)
                )
            )
        ).scalar_one_or_none()

        script_out = None
        if script is not None:
            donor_ids = {
                c.donor_id
                for b in script.blocks
                for c in b.candidates
                if c.donor_id
            }
            donors: dict[str, Donor] = {}
            if donor_ids:
                donors = {
                    d.id: d
                    for d in (
                        await session.execute(
                            select(Donor).where(Donor.id.in_(donor_ids))
                        )
                    ).scalars()
                }
            script_out = {
                "version": script.version,
                "title": script.title,
                "description": script.description,
                "hook_pattern": script.hook_pattern,
                "blocks": [
                    {
                        "id": b.id,
                        "ordinal": b.ordinal,
                        "role": b.role,
                        "text_en": b.text_en,
                        "frame_desc": b.frame_desc,
                        "search_keys": b.search_keys,
                        "fx": b.fx,
                        "status": b.status.value,
                        "t_start": b.t_start,
                        "t_end": b.t_end,
                        "candidates": [
                            {
                                "id": c.id,
                                "rank": c.rank,
                                "url": media_url(c.file_path),
                                "duration": c.duration,
                                "motion_score": c.motion_score,
                                "chosen": c.chosen,
                                "manual_note": c.manual_note,
                                "donor": (
                                    {
                                        "yt_video_id": donors[c.donor_id].yt_video_id,
                                        "yt_channel": donors[c.donor_id].yt_channel,
                                        "yt_title": donors[c.donor_id].yt_title,
                                        "is_mock": donors[c.donor_id].is_mock,
                                    }
                                    if c.donor_id and c.donor_id in donors
                                    else None
                                ),
                            }
                            for c in b.candidates
                        ],
                    }
                    for b in script.blocks
                ],
            }

        voice = (
            await session.execute(
                select(VoiceTrack)
                .where(VoiceTrack.job_id == job.id)
                .order_by(desc(VoiceTrack.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()

        renders = (
            (
                await session.execute(
                    select(Render)
                    .where(Render.job_id == job.id)
                    .order_by(Render.created_at)
                )
            )
            .scalars()
            .all()
        )

        step_runs = (
            (
                await session.execute(
                    select(StepRun)
                    .where(StepRun.job_id == job.id)
                    .order_by(desc(StepRun.started_at))
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        step_runs = list(reversed(step_runs))

        detail = job_summary(job, find_open_gate(gates))
        detail.update(
            {
                "batch_id": job.batch_id,
                "gates": [
                    {
                        "type": g.type.value,
                        "status": g.status.value,
                        "approved_by": g.approved_by,
                        "approved_at": iso(g.approved_at),
                    }
                    for g in gates
                ],
                "script": script_out,
                "voice": (
                    {
                        "url": media_url(voice.wav_path),
                        "duration": voice.duration,
                        "is_mock": voice.is_mock,
                    }
                    if voice
                    else None
                ),
                "renders": [
                    {
                        "id": r.id,
                        "kind": r.kind,
                        "version": r.version,
                        "url": media_url(r.file_path),
                        "preview_url": media_url(r.preview_path),
                        "changelog": r.changelog,
                        "qc": r.qc,
                        "created_at": iso(r.created_at),
                    }
                    for r in renders
                ],
                "step_runs": [
                    {
                        "step": s.step.value,
                        "ok": s.ok,
                        "detail": s.detail,
                        "started_at": iso(s.started_at),
                        "finished_at": iso(s.finished_at),
                    }
                    for s in step_runs
                ],
            }
        )
        return detail

    @app.post("/api/jobs/{job_id}/retry")
    async def retry_job(
        job_id: str,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        job = await get_job_or_404(session, job_id)
        if job.status == JobStatus.FAILED:
            last_run = (
                await session.execute(
                    select(StepRun)
                    .where(StepRun.job_id == job.id)
                    .order_by(desc(StepRun.started_at))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if last_run is not None and last_run.step in STEP_RESTART:
                new_status, task = STEP_RESTART[last_run.step]
            else:
                new_status, task = JobStatus.SCRIPTING, "run_writer"
        elif job.status in STATUS_TASK:
            new_status, task = job.status, STATUS_TASK[job.status]
        else:
            raise HTTPException(
                status_code=409, detail=f"cannot retry job in status {job.status.value}"
            )
        job.status = new_status
        job.error = ""
        await write_event(
            session,
            "job_status",
            job_id=job.id,
            batch_id=job.batch_id,
            payload={"status": new_status.value, "retry": True},
        )
        await session.commit()
        await queue.enqueue(task, job.id)
        return {}

    # ---------------------------------------------------------- gates

    @app.post("/api/jobs/{job_id}/gates/{gate_type}/approve")
    async def approve_gate(
        job_id: str,
        gate_type: str,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        try:
            gtype = GateType(gate_type)
        except ValueError:
            raise HTTPException(status_code=404, detail="unknown gate type")
        job = await get_job_or_404(session, job_id)
        expected_status, next_status, task = GATE_TRANSITIONS[gtype]
        if job.status != expected_status:
            raise HTTPException(
                status_code=409,
                detail=f"gate {gtype.value} is not open (job status: {job.status.value})",
            )
        gate = (
            await session.execute(
                select(Gate).where(Gate.job_id == job.id, Gate.type == gtype)
            )
        ).scalar_one_or_none()
        if gate is None:
            raise HTTPException(status_code=404, detail="gate not found")

        if gtype == GateType.CLIPS:
            # каждый блок активного сценария: либо выбран кандидат, либо needs_footage
            script = (
                await session.execute(
                    select(Script)
                    .where(Script.job_id == job.id)
                    .order_by(desc(Script.version))
                    .limit(1)
                    .options(
                        selectinload(Script.blocks).selectinload(Block.candidates)
                    )
                )
            ).scalar_one_or_none()
            if script is None:
                raise HTTPException(status_code=409, detail="job has no script")
            for b in script.blocks:
                if b.status == BlockStatus.NEEDS_FOOTAGE:
                    continue
                if not any(c.chosen for c in b.candidates):
                    raise HTTPException(
                        status_code=409,
                        detail=f"block {b.ordinal} has no chosen candidate",
                    )

        gate.status = GateStatus.APPROVED
        gate.approved_by = user.id
        gate.approved_at = datetime.now(timezone.utc)
        job.status = next_status
        await write_event(
            session,
            "job_status",
            job_id=job.id,
            batch_id=job.batch_id,
            payload={
                "status": next_status.value,
                "gate": gtype.value,
                "approved_by": user.login,
            },
        )
        await session.commit()
        if task is not None:
            await queue.enqueue(task, job.id)
        return {}

    # ---------------------------------------------------------- blocks

    async def _get_block_of_job(
        session: AsyncSession, job_id: str, block_id: str
    ) -> Block:
        block = (
            await session.execute(
                select(Block)
                .join(Script, Block.script_id == Script.id)
                .where(Block.id == block_id, Script.job_id == job_id)
                .options(selectinload(Block.candidates))
            )
        ).scalar_one_or_none()
        if block is None:
            raise HTTPException(status_code=404, detail="block not found")
        return block

    @app.post("/api/jobs/{job_id}/blocks/{block_id}/choose")
    async def choose_candidate(
        job_id: str,
        block_id: str,
        body: ChooseIn,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        job = await get_job_or_404(session, job_id)
        block = await _get_block_of_job(session, job.id, block_id)
        target = next(
            (c for c in block.candidates if c.id == body.candidate_id), None
        )
        if target is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        for c in block.candidates:
            c.chosen = c.id == target.id
        block.status = BlockStatus.OK
        await session.commit()
        return {}

    @app.post("/api/jobs/{job_id}/blocks/{block_id}/candidates")
    async def extra_candidates(
        job_id: str,
        block_id: str,
        body: CandidatesIn,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        job = await get_job_or_404(session, job_id)
        await _get_block_of_job(session, job.id, block_id)
        if not body.query and not body.yt_url:
            raise HTTPException(status_code=422, detail="query or yt_url required")
        await queue.enqueue(
            "extra_candidates",
            job.id,
            block_id,
            query=body.query,
            yt_url=body.yt_url,
        )
        return {"task": "queued"}

    # ---------------------------------------------------------- chat

    @app.get("/api/jobs/{job_id}/chat")
    async def chat_list(
        job_id: str,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        job = await get_job_or_404(session, job_id)
        messages = (
            (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.job_id == job.id)
                    .order_by(ChatMessage.created_at)
                )
            )
            .scalars()
            .all()
        )
        user_ids = {m.user_id for m in messages if m.user_id}
        logins: dict[str, str] = {}
        if user_ids:
            logins = {
                u.id: u.login
                for u in (
                    await session.execute(select(User).where(User.id.in_(user_ids)))
                ).scalars()
            }
        return [
            {
                "id": m.id,
                "role": m.role,
                "text": m.text,
                "extra": m.extra,
                "created_at": iso(m.created_at),
                "user": logins.get(m.user_id) if m.user_id else None,
            }
            for m in messages
        ]

    @app.post("/api/jobs/{job_id}/chat")
    async def chat_post(
        job_id: str,
        body: ChatIn,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        job = await get_job_or_404(session, job_id)
        msg = ChatMessage(job_id=job.id, user_id=user.id, role="user", text=body.text)
        session.add(msg)
        await session.commit()
        await queue.enqueue("agent_reply", job.id, msg.id)
        return {"message_id": msg.id}

    async def _get_message_of_job(
        session: AsyncSession, job_id: str, message_id: str
    ) -> ChatMessage:
        msg = (
            await session.execute(
                select(ChatMessage).where(
                    ChatMessage.id == message_id, ChatMessage.job_id == job_id
                )
            )
        ).scalar_one_or_none()
        if msg is None:
            raise HTTPException(status_code=404, detail="message not found")
        return msg

    @app.post("/api/jobs/{job_id}/chat/{message_id}/confirm")
    async def chat_confirm(
        job_id: str,
        message_id: str,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        job = await get_job_or_404(session, job_id)
        msg = await _get_message_of_job(session, job.id, message_id)
        msg.extra = {**(msg.extra or {}), "plan_status": "confirmed"}
        await session.commit()
        await queue.enqueue("apply_plan", job.id, msg.id)
        return {}

    @app.post("/api/jobs/{job_id}/chat/{message_id}/reject")
    async def chat_reject(
        job_id: str,
        message_id: str,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        job = await get_job_or_404(session, job_id)
        msg = await _get_message_of_job(session, job.id, message_id)
        msg.extra = {**(msg.extra or {}), "plan_status": "rejected"}
        await session.commit()
        return {}

    # ---------------------------------------------------------- settings

    @app.get("/api/settings")
    async def settings_list(
        user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
    ):
        return await settings_store.list_settings(session)

    @app.put("/api/settings/{key}")
    async def settings_put(
        key: str,
        body: SettingIn,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        try:
            await settings_store.set_setting(session, key, body.value)
        except KeyError:
            raise HTTPException(status_code=400, detail=f"unknown setting key: {key}")
        await session.commit()
        return {}

    @app.get("/api/settings/providers")
    async def settings_providers(
        user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
    ):
        return await settings_store.providers_status(session)

    # ---------------------------------------------------------- users

    @app.get("/api/users")
    async def users_list(
        user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
    ):
        users = (
            (await session.execute(select(User).order_by(User.created_at)))
            .scalars()
            .all()
        )
        return [
            {"id": u.id, "login": u.login, "created_at": iso(u.created_at)}
            for u in users
        ]

    @app.post("/api/users")
    async def users_create(
        body: UserIn,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        if not body.login or not body.password:
            raise HTTPException(status_code=422, detail="login and password required")
        exists = (
            await session.execute(select(User).where(User.login == body.login))
        ).scalar_one_or_none()
        if exists is not None:
            raise HTTPException(status_code=409, detail="login already exists")
        new = User(login=body.login, pw_hash=hash_password(body.password))
        session.add(new)
        await session.commit()
        return {"id": new.id, "login": new.login}

    @app.delete("/api/users/{user_id}")
    async def users_delete(
        user_id: str,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        if user_id == user.id:
            raise HTTPException(status_code=400, detail="cannot delete yourself")
        target = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")
        await session.delete(target)
        await session.commit()
        return {}

    # ---------------------------------------------------------- events

    def event_dict(e: Event) -> dict:
        return {
            "id": e.id,
            "type": e.type,
            "job_id": e.job_id,
            "batch_id": e.batch_id,
            "payload": e.payload,
            "created_at": iso(e.created_at),
        }

    @app.get("/api/events")
    async def events_poll(
        after: int = 0,
        user: User = Depends(current_user),
        session: AsyncSession = Depends(get_session),
    ):
        events = (
            (
                await session.execute(
                    select(Event).where(Event.id > after).order_by(Event.id).limit(500)
                )
            )
            .scalars()
            .all()
        )
        return [event_dict(e) for e in events]

    @app.get("/api/events/stream")
    async def events_stream(
        request: Request,
        after: int | None = None,
        user: User = Depends(current_user),
    ):
        sessionmaker = get_sessionmaker()

        async def gen():
            last_id = after
            if last_id is None:
                async with sessionmaker() as s:
                    from sqlalchemy import func as sqlfunc

                    last_id = (
                        await s.execute(select(sqlfunc.max(Event.id)))
                    ).scalar() or 0
            while True:
                if await request.is_disconnected():
                    return
                async with sessionmaker() as s:
                    events = (
                        (
                            await s.execute(
                                select(Event)
                                .where(Event.id > last_id)
                                .order_by(Event.id)
                                .limit(500)
                            )
                        )
                        .scalars()
                        .all()
                    )
                for e in events:
                    last_id = e.id
                    yield f"id: {e.id}\ndata: {json.dumps(event_dict(e), ensure_ascii=False)}\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---------------------------------------------------------- media (за сессией — см. middleware)

    data_dir = os.environ.get("DATA_DIR", "/home/user/shortforge-data")
    os.makedirs(data_dir, exist_ok=True)
    app.mount("/media", StaticFiles(directory=data_dir, check_dir=False), name="media")

    return app


app = create_app()
