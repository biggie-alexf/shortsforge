"""E2E весь пайплайн на mock-провайдерах, БЕЗ сети и БЕЗ API-ключей.

writer -> approve(script) -> hunter -> cutter -> approve(clips) -> voicer ->
rough -> approve(rough) -> master. Гейты апрувятся «руками» (как это сделает api).
"""
from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from shortforge.db import get_sessionmaker
from shortforge.media.ffutil import abs_path
from shortforge.models import (
    Batch,
    Block,
    ClipCandidate,
    Donor,
    Gate,
    GateStatus,
    GateType,
    JobStatus,
    Render,
    Script,
    StepRun,
    VideoFormat,
    VideoJob,
    VoiceTrack,
)
from shortforge.pipeline.worker import (
    run_cutter,
    run_hunter,
    run_master,
    run_rough,
    run_voicer,
    run_writer,
)

pytestmark = pytest.mark.asyncio


async def _get_job(job_id: str) -> VideoJob:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(VideoJob).where(VideoJob.id == job_id))
        ).scalar_one()


async def _approve(job_id: str, gtype: GateType) -> None:
    async with get_sessionmaker()() as s:
        gate = (
            await s.execute(
                select(Gate).where(Gate.job_id == job_id, Gate.type == gtype)
            )
        ).scalar_one()
        assert gate.status == GateStatus.OPEN, f"гейт {gtype} не открыт"
        gate.status = GateStatus.APPROVED
        await s.commit()


async def test_e2e_mock():
    t0 = time.monotonic()

    async with get_sessionmaker()() as s:
        batch = Batch(title="e2e-mock")
        s.add(batch)
        await s.flush()
        job = VideoJob(
            batch_id=batch.id,
            game="Steal a Brainrot",
            idea="secret ramadan brainrot",
            format=VideoFormat.A,
        )
        s.add(job)
        await s.commit()
        job_id = job.id

    # -------- writer -> gate_script
    detail = await run_writer({}, job_id)
    assert not detail.startswith("FAILED"), detail
    job = await _get_job(job_id)
    assert job.status == JobStatus.GATE_SCRIPT, job.error
    async with get_sessionmaker()() as s:
        script = (
            await s.execute(select(Script).where(Script.job_id == job_id))
        ).scalar_one()
        blocks = list(
            (
                await s.execute(
                    select(Block)
                    .where(Block.script_id == script.id)
                    .order_by(Block.ordinal)
                )
            ).scalars()
        )
    assert len(blocks) == 7  # формат A
    assert [b.role for b in blocks] == [
        "hook", "setup", "evidence", "evidence", "cta", "twist", "loop",
    ]

    # -------- approve script -> hunter -> cutter (в тесте цепочка руками)
    await _approve(job_id, GateType.SCRIPT)
    detail = await run_hunter({}, job_id)
    assert not detail.startswith("FAILED"), detail
    job = await _get_job(job_id)
    assert job.status == JobStatus.CUTTING  # hunter сам двигает в cutting
    async with get_sessionmaker()() as s:
        donors = list(
            (await s.execute(select(Donor).where(Donor.job_id == job_id))).scalars()
        )
    assert donors, "hunter не скачал доноров"
    assert len({d.yt_video_id for d in donors}) >= 2  # несколько доноров (ADR-006)
    for d in donors:
        assert abs_path(d.file_path).exists()

    detail = await run_cutter({}, job_id)
    assert not detail.startswith("FAILED"), detail
    job = await _get_job(job_id)
    assert job.status == JobStatus.GATE_CLIPS
    async with get_sessionmaker()() as s:
        for b in blocks:
            cands = list(
                (
                    await s.execute(
                        select(ClipCandidate).where(ClipCandidate.block_id == b.id)
                    )
                ).scalars()
            )
            assert 2 <= len(cands) <= 3, f"блок {b.ordinal}: {len(cands)} кандидатов"
            assert sum(1 for c in cands if c.chosen) == 1
            for c in cands:
                assert 3.0 <= c.duration <= 5.0  # сниппеты 3-5 c (и ≤7 с от донора)
                assert abs_path(c.file_path).exists()

    # -------- approve clips -> voicer -> rough
    await _approve(job_id, GateType.CLIPS)
    detail = await run_voicer({}, job_id)
    assert not detail.startswith("FAILED"), detail
    job = await _get_job(job_id)
    assert job.status == JobStatus.ROUGH_RENDER  # voicer сам двигает в rough_render
    async with get_sessionmaker()() as s:
        voice = (
            await s.execute(select(VoiceTrack).where(VoiceTrack.job_id == job_id))
        ).scalar_one()
        blocks2 = list(
            (
                await s.execute(
                    select(Block)
                    .where(Block.script_id == script.id)
                    .order_by(Block.ordinal)
                )
            ).scalars()
        )
    assert voice.is_mock and abs_path(voice.wav_path).exists()
    assert voice.duration > 10
    for b in blocks2:  # voicer разложил тайминги по блокам
        assert b.t_start is not None and b.t_end is not None and b.t_end > b.t_start

    detail = await run_rough({}, job_id)
    assert not detail.startswith("FAILED"), detail
    job = await _get_job(job_id)
    assert job.status == JobStatus.GATE_ROUGH

    # -------- approve rough -> master
    await _approve(job_id, GateType.ROUGH)
    detail = await run_master({}, job_id)
    assert not detail.startswith("FAILED"), detail
    job = await _get_job(job_id)
    assert job.status == JobStatus.GATE_MASTER
    assert job.current_version == 1

    # -------- итоговые проверки
    async with get_sessionmaker()() as s:
        renders = list(
            (await s.execute(select(Render).where(Render.job_id == job_id))).scalars()
        )
        runs = list(
            (await s.execute(select(StepRun).where(StepRun.job_id == job_id))).scalars()
        )
    rough = next(r for r in renders if r.kind == "rough")
    master = next(r for r in renders if r.kind == "master")

    master_path = abs_path(master.file_path)
    rough_path = abs_path(rough.file_path)
    assert master_path.exists() and rough_path.exists()

    assert master.qc, "нет Render.qc у мастера"
    assert 15.0 <= master.qc["duration"] <= 45.0, master.qc
    assert (master.qc["width"], master.qc["height"]) == (1080, 1920)
    assert master.qc["lufs"] is not None
    assert abs(master.qc["lufs"] - (-14.0)) <= 1.5, master.qc  # loudnorm до -14 LUFS
    assert abs_path(master.preview_path).exists()  # превью-jpg первого кадра

    # сабы/музыка/эффекты вшиты: мастер тяжелее черновика
    assert master_path.stat().st_size > rough_path.stat().st_size

    assert all(r.ok for r in runs), [(r.step, r.detail) for r in runs if not r.ok]
    assert len(runs) == 6  # writer, hunter, cutter, voicer, rough, master

    elapsed = time.monotonic() - t0
    print(f"\nE2E mock pipeline: {elapsed:.1f}s, master: {master_path}")
