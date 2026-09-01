"""Batches/jobs: создание батча, JobDetail, гейты, blocks, chat, retry, events."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from shortforge import models
from shortforge.db import get_sessionmaker
from shortforge.models import (
    Block,
    BlockStatus,
    ChatMessage,
    ClipCandidate,
    Donor,
    Gate,
    GateStatus,
    GateType,
    JobStatus,
    Script,
    StepName,
    StepRun,
    VideoJob,
)

pytestmark = pytest.mark.asyncio


async def _create_batch(client) -> tuple[str, list[str]]:
    resp = await client.post(
        "/api/batches",
        json={
            "title": "batch-1",
            "jobs": [
                {"game": "Steal a Brainrot", "idea": "secret room", "format": "A"},
                {"game": "Grow a Garden", "idea": "meme monologue", "format": "B"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    batch_id = resp.json()["id"]
    async with get_sessionmaker()() as s:
        jobs = (
            (await s.execute(select(VideoJob).where(VideoJob.batch_id == batch_id)))
            .scalars()
            .all()
        )
    return batch_id, [j.id for j in jobs]


async def test_create_batch(auth_client):
    client = auth_client
    batch_id, job_ids = await _create_batch(client)
    assert len(job_ids) == 2

    async with get_sessionmaker()() as s:
        jobs = (
            (await s.execute(select(VideoJob).where(VideoJob.batch_id == batch_id)))
            .scalars()
            .all()
        )
        assert all(j.status == JobStatus.QUEUED for j in jobs)
        for j in jobs:
            gates = (
                (await s.execute(select(Gate).where(Gate.job_id == j.id)))
                .scalars()
                .all()
            )
            assert len(gates) == 4
            assert {g.type for g in gates} == set(GateType)
            assert all(g.status == GateStatus.PENDING for g in gates)
        events = (
            (await s.execute(select(models.Event))).scalars().all()
        )
        assert len([e for e in events if e.type == "job_status"]) == 2

    # run_writer поставлен для каждого job-а
    writer_calls = [c for c in client.enqueued if c[0] == "run_writer"]
    assert sorted(c[1][0] for c in writer_calls) == sorted(job_ids)

    # список батчей
    resp = await client.get("/api/batches")
    assert resp.status_code == 200
    batches = resp.json()
    assert batches[0]["id"] == batch_id
    summary = batches[0]["jobs"][0]
    assert set(summary) == {
        "id", "game", "idea", "format", "status", "current_version",
        "open_gate", "error",
    }
    assert summary["status"] == "queued"
    assert summary["open_gate"] is None


async def test_batch_empty_jobs_rejected(auth_client):
    resp = await auth_client.post("/api/batches", json={"jobs": []})
    assert resp.status_code == 422


async def test_batches_require_auth(client):
    resp = await client.get("/api/batches")
    assert resp.status_code == 401
    resp = await client.post("/api/batches", json={"jobs": [{"game": "g", "idea": "i"}]})
    assert resp.status_code == 401


async def _seed_script(job_id: str, with_candidates: bool = True) -> dict:
    """Скрипт v1 c двумя блоками; у первого — 2 кандидата (donor у первого)."""
    out: dict = {}
    async with get_sessionmaker()() as s:
        script = Script(job_id=job_id, version=1, title="T", hook_pattern="secret")
        s.add(script)
        await s.flush()
        b1 = Block(script_id=script.id, ordinal=1, role="hook", text_en="Hook text")
        b2 = Block(script_id=script.id, ordinal=2, role="cta", text_en="CTA text")
        s.add_all([b1, b2])
        await s.flush()
        out["script_id"] = script.id
        out["b1"], out["b2"] = b1.id, b2.id
        if with_candidates:
            donor = Donor(
                job_id=job_id, yt_video_id="abc123", yt_channel="Ch",
                yt_title="Video", is_mock=True,
            )
            s.add(donor)
            await s.flush()
            c1 = ClipCandidate(
                block_id=b1.id, donor_id=donor.id, rank=1,
                file_path="batch/job/candidates/c1.mp4", duration=3.5,
                motion_score=0.8,
            )
            c2 = ClipCandidate(
                block_id=b1.id, rank=2,
                file_path="batch/job/candidates/c2.mp4", duration=2.0,
            )
            s.add_all([c1, c2])
            await s.flush()
            out["c1"], out["c2"] = c1.id, c2.id
        await s.commit()
    return out


async def _set_job_status(job_id: str, status: JobStatus, open_gate: GateType | None = None):
    async with get_sessionmaker()() as s:
        job = (
            await s.execute(select(VideoJob).where(VideoJob.id == job_id))
        ).scalar_one()
        job.status = status
        if open_gate is not None:
            gate = (
                await s.execute(
                    select(Gate).where(Gate.job_id == job_id, Gate.type == open_gate)
                )
            ).scalar_one()
            gate.status = GateStatus.OPEN
        await s.commit()


async def test_job_detail(auth_client):
    client = auth_client
    batch_id, job_ids = await _create_batch(client)
    job_id = job_ids[0]
    ids = await _seed_script(job_id)
    await _set_job_status(job_id, JobStatus.GATE_SCRIPT, GateType.SCRIPT)

    resp = await client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    d = resp.json()
    assert d["batch_id"] == batch_id
    assert d["status"] == "gate_script"
    assert d["open_gate"] == "script"
    assert [g["type"] for g in d["gates"]] == ["script", "clips", "rough", "master"]
    assert d["script"]["version"] == 1
    blocks = d["script"]["blocks"]
    assert len(blocks) == 2
    cands = blocks[0]["candidates"]
    assert len(cands) == 2
    assert cands[0]["url"] == "/media/batch/job/candidates/c1.mp4"
    assert cands[0]["donor"]["yt_video_id"] == "abc123"
    assert cands[1]["donor"] is None
    assert d["voice"] is None
    assert d["renders"] == []
    assert d["step_runs"] == []

    resp = await client.get("/api/jobs/nope")
    assert resp.status_code == 404


async def test_gate_script_approve(auth_client, admin):
    client = auth_client
    _, job_ids = await _create_batch(client)
    job_id = job_ids[0]

    # гейт ещё не открыт (status=queued) -> 409
    resp = await client.post(f"/api/jobs/{job_id}/gates/script/approve")
    assert resp.status_code == 409

    await _set_job_status(job_id, JobStatus.GATE_SCRIPT, GateType.SCRIPT)
    resp = await client.post(f"/api/jobs/{job_id}/gates/script/approve")
    assert resp.status_code == 200

    async with get_sessionmaker()() as s:
        job = (
            await s.execute(select(VideoJob).where(VideoJob.id == job_id))
        ).scalar_one()
        assert job.status == JobStatus.HUNTING
        gate = (
            await s.execute(
                select(Gate).where(Gate.job_id == job_id, Gate.type == GateType.SCRIPT)
            )
        ).scalar_one()
        assert gate.status == GateStatus.APPROVED
        assert gate.approved_by == admin.id
        assert gate.approved_at is not None

    assert ("run_hunter", (job_id,), {}) in client.enqueued


async def test_gate_clips_409_then_approve(auth_client):
    client = auth_client
    _, job_ids = await _create_batch(client)
    job_id = job_ids[0]
    ids = await _seed_script(job_id)
    await _set_job_status(job_id, JobStatus.GATE_CLIPS, GateType.CLIPS)

    # ни один кандидат не выбран, b2 вообще без кандидатов -> 409
    resp = await client.post(f"/api/jobs/{job_id}/gates/clips/approve")
    assert resp.status_code == 409

    # выбираем кандидата для b1
    resp = await client.post(
        f"/api/jobs/{job_id}/blocks/{ids['b1']}/choose",
        json={"candidate_id": ids["c2"]},
    )
    assert resp.status_code == 200
    async with get_sessionmaker()() as s:
        c1 = (
            await s.execute(select(ClipCandidate).where(ClipCandidate.id == ids["c1"]))
        ).scalar_one()
        c2 = (
            await s.execute(select(ClipCandidate).where(ClipCandidate.id == ids["c2"]))
        ).scalar_one()
        assert not c1.chosen and c2.chosen

    # b2 всё ещё без решения -> 409
    resp = await client.post(f"/api/jobs/{job_id}/gates/clips/approve")
    assert resp.status_code == 409

    # b2 -> needs_footage
    async with get_sessionmaker()() as s:
        b2 = (await s.execute(select(Block).where(Block.id == ids["b2"]))).scalar_one()
        b2.status = BlockStatus.NEEDS_FOOTAGE
        await s.commit()

    resp = await client.post(f"/api/jobs/{job_id}/gates/clips/approve")
    assert resp.status_code == 200
    async with get_sessionmaker()() as s:
        job = (
            await s.execute(select(VideoJob).where(VideoJob.id == job_id))
        ).scalar_one()
        assert job.status == JobStatus.VOICING
    assert ("run_voicer", (job_id,), {}) in client.enqueued


async def test_gate_rough_and_master(auth_client):
    client = auth_client
    _, job_ids = await _create_batch(client)
    job_id = job_ids[0]

    await _set_job_status(job_id, JobStatus.GATE_ROUGH, GateType.ROUGH)
    resp = await client.post(f"/api/jobs/{job_id}/gates/rough/approve")
    assert resp.status_code == 200
    assert ("run_master", (job_id,), {}) in client.enqueued

    await _set_job_status(job_id, JobStatus.GATE_MASTER, GateType.MASTER)
    resp = await client.post(f"/api/jobs/{job_id}/gates/master/approve")
    assert resp.status_code == 200
    async with get_sessionmaker()() as s:
        job = (
            await s.execute(select(VideoJob).where(VideoJob.id == job_id))
        ).scalar_one()
        assert job.status == JobStatus.DONE


async def test_extra_candidates(auth_client):
    client = auth_client
    _, job_ids = await _create_batch(client)
    job_id = job_ids[0]
    ids = await _seed_script(job_id)

    resp = await client.post(
        f"/api/jobs/{job_id}/blocks/{ids['b1']}/candidates", json={}
    )
    assert resp.status_code == 422

    resp = await client.post(
        f"/api/jobs/{job_id}/blocks/{ids['b1']}/candidates",
        json={"query": "obby speedrun"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"task": "queued"}
    assert (
        "extra_candidates",
        (job_id, ids["b1"]),
        {"query": "obby speedrun", "yt_url": None},
    ) in client.enqueued


async def test_chat_flow(auth_client, admin):
    client = auth_client
    _, job_ids = await _create_batch(client)
    job_id = job_ids[0]

    resp = await client.post(f"/api/jobs/{job_id}/chat", json={"text": "make hook shorter"})
    assert resp.status_code == 200
    message_id = resp.json()["message_id"]
    assert ("agent_reply", (job_id, message_id), {}) in client.enqueued

    resp = await client.get(f"/api/jobs/{job_id}/chat")
    assert resp.status_code == 200
    msgs = resp.json()
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["user"] == "admin"

    # confirm -> apply_plan
    resp = await client.post(f"/api/jobs/{job_id}/chat/{message_id}/confirm")
    assert resp.status_code == 200
    assert ("apply_plan", (job_id, message_id), {}) in client.enqueued

    # reject -> plan_status=rejected
    resp = await client.post(f"/api/jobs/{job_id}/chat/{message_id}/reject")
    assert resp.status_code == 200
    async with get_sessionmaker()() as s:
        msg = (
            await s.execute(select(ChatMessage).where(ChatMessage.id == message_id))
        ).scalar_one()
        assert msg.extra["plan_status"] == "rejected"


async def test_retry_failed(auth_client):
    client = auth_client
    _, job_ids = await _create_batch(client)
    job_id = job_ids[0]

    async with get_sessionmaker()() as s:
        job = (
            await s.execute(select(VideoJob).where(VideoJob.id == job_id))
        ).scalar_one()
        job.status = JobStatus.FAILED
        job.error = "boom"
        s.add(StepRun(job_id=job_id, step=StepName.HUNTER, ok=False, detail="boom"))
        await s.commit()

    resp = await client.post(f"/api/jobs/{job_id}/retry")
    assert resp.status_code == 200
    async with get_sessionmaker()() as s:
        job = (
            await s.execute(select(VideoJob).where(VideoJob.id == job_id))
        ).scalar_one()
        assert job.status == JobStatus.HUNTING
        assert job.error == ""
    assert ("run_hunter", (job_id,), {}) in client.enqueued


async def test_events_poll(auth_client):
    client = auth_client
    _, job_ids = await _create_batch(client)

    resp = await client.get("/api/events?after=0")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) >= 2
    assert all(
        set(e) == {"id", "type", "job_id", "batch_id", "payload", "created_at"}
        for e in events
    )
    last_id = events[-1]["id"]
    resp = await client.get(f"/api/events?after={last_id}")
    assert resp.json() == []
