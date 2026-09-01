"""Чат-агент правок (ADR-005): Claude tool-use поверх операций пайплайна.

Схема: пользователь пишет в чат -> agent_reply строит паспорт видео, зовёт LLM с
инструментами -> план сохраняется в ChatMessage.extra.plan (plan_status=proposed).
Пользователь жмёт «Выполнить» -> apply_plan исполняет шаги и сам решает, какие
шаги пайплайна перезапустить (см. _route_rerun).
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..media.ffutil import job_dir
from ..models import (
    Block, ChatMessage, ClipCandidate, GateStatus, GateType, JobStatus, Script,
    VideoJob,
)
from ..providers.base import ScriptDraft
from ..providers.factory import get_providers
from .events import emit
from .steps import cutter
from .steps.common import active_script, script_blocks

log = logging.getLogger("shortforge.agent")

TOOLS: list[dict] = [
    {
        "name": "edit_script",
        "description": "Переписать сценарий (или один блок) по инструкции. Создаёт новую версию сценария; после этого потребуется переозвучка и перерендер.",
        "input_schema": {"type": "object", "properties": {
            "instruction": {"type": "string", "description": "Что изменить (по-английски или по-русски)"},
            "block_ordinal": {"type": "integer", "description": "Номер блока 1..N; не указывать = весь сценарий"},
        }, "required": ["instruction"]},
    },
    {
        "name": "replace_clip",
        "description": "Заменить выбранный клип блока на другого кандидата.",
        "input_schema": {"type": "object", "properties": {
            "block_ordinal": {"type": "integer"},
            "candidate_rank": {"type": "integer", "description": "Ранг кандидата; не указывать = следующий по порядку"},
        }, "required": ["block_ordinal"]},
    },
    {
        "name": "add_candidates",
        "description": "Найти и нарезать новых кандидатов под блок (новый поисковый запрос или конкретное YouTube-видео).",
        "input_schema": {"type": "object", "properties": {
            "block_ordinal": {"type": "integer"},
            "query": {"type": "string"},
            "yt_url": {"type": "string"},
        }, "required": ["block_ordinal"]},
    },
    {
        "name": "regen_voice",
        "description": "Переозвучить текущий сценарий заново (тот же голос).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "restyle_subs",
        "description": "Правки сабов этого видео: словарь замен слово->написание (например исправить имя из игры).",
        "input_schema": {"type": "object", "properties": {
            "replacements": {"type": "object"},
            "note": {"type": "string"},
        }},
    },
    {
        "name": "retime",
        "description": "Ужать или растянуть видео под целевой хронометраж: сценарий переписывается под нужное число слов.",
        "input_schema": {"type": "object", "properties": {
            "target_seconds": {"type": "number"},
        }, "required": ["target_seconds"]},
    },
    {
        "name": "add_sfx",
        "description": "Добавить звуковой акцент в начало блока.",
        "input_schema": {"type": "object", "properties": {
            "block_ordinal": {"type": "integer"},
            "name": {"type": "string", "enum": ["impact", "tick", "sting", "glitch"]},
        }, "required": ["block_ordinal", "name"]},
    },
    {
        "name": "set_music",
        "description": "Изменить фоновую музыку: громкость 0..1 и/или настроение.",
        "input_schema": {"type": "object", "properties": {
            "volume": {"type": "number"},
            "mood": {"type": "string"},
        }},
    },
    {
        "name": "rerender",
        "description": "Принудительно пересобрать черновик или мастер без других изменений.",
        "input_schema": {"type": "object", "properties": {
            "step": {"type": "string", "enum": ["rough", "master"]},
        }, "required": ["step"]},
    },
]

SYSTEM_RU = """Ты — монтажёр-ассистент сервиса ShortForge (вертикальные Roblox-видео).
Пользователь просит правки к конкретному видео. Твоя задача — превратить просьбу в план из
вызовов инструментов (1-4 шага) и коротко объяснить его по-русски. Правила производства:
формат A = «секрет/разбор» 18-45с, формат B = «мемный монолог» 25-35с; хук в первых 2с;
темп ~2.8 слова/сек; сабы капсом; SFX на акцентах. Высокоуровневые просьбы раскладывай сам:
«сделай энергичнее» = add_sfx impact на хук + set_music громче + rerender;
«сделай короче до N сек» = retime; «добавь мемов/смешных звуков» = add_sfx sting/glitch на
evidence-блоки. Если просьба неясна — задай уточняющий вопрос текстом без инструментов.
Отвечай кратко. Текст сценария всегда на английском, обсуждение — на русском."""


async def _passport(session: AsyncSession, job: VideoJob) -> str:
    script = await active_script(session, job)
    lines = [
        f"Видео: игра={job.game!r}, идея={job.idea!r}, формат={job.format.value}, "
        f"статус={job.status.value}",
    ]
    if script:
        lines.append(f"Сценарий v{script.version}: {script.title!r}")
        for b in await script_blocks(session, script):
            cands = (
                await session.execute(
                    select(ClipCandidate).where(ClipCandidate.block_id == b.id)
                )
            ).scalars().all()
            chosen = next((c.rank for c in cands if c.chosen), None)
            lines.append(
                f"  блок {b.ordinal} [{b.role}] ({b.status.value}): {b.text_en!r} | "
                f"кадр: {b.frame_desc} | кандидатов {len(cands)}, выбран rank={chosen} | "
                f"fx={b.fx}"
            )
    return "\n".join(lines)


async def _history(session: AsyncSession, job_id: str, limit: int = 16) -> list[dict]:
    msgs = (
        await session.execute(
            select(ChatMessage)
            .where(ChatMessage.job_id == job_id, ChatMessage.role.in_(["user", "agent"]))
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    out: list[dict] = []
    for m in reversed(msgs):
        role = "user" if m.role == "user" else "assistant"
        text = m.text or "(план)"
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": role, "content": text})
    if not out or out[-1]["role"] != "user":
        out.append({"role": "user", "content": "(продолжи)"})
    if out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": "(начало диалога)"})
    return out


async def run_agent_reply(session: AsyncSession, job: VideoJob, message_id: str) -> str:
    providers = await get_providers(session)
    system = SYSTEM_RU + "\n\n=== ПАСПОРТ ВИДЕО ===\n" + await _passport(session, job)
    messages = await _history(session, job.id)
    resp = await providers.llm.agent_turn(system=system, messages=messages, tools=TOOLS)

    texts = [c.get("text", "") for c in resp.get("content", []) if c.get("type") == "text"]
    plan = [
        {"tool": c["name"], "args": c.get("input", {})}
        for c in resp.get("content", [])
        if c.get("type") == "tool_use"
    ]
    text = "\n".join(t for t in texts if t).strip()
    extra: dict = {"reply_to": message_id}
    if plan:
        extra["plan"] = plan
        extra["plan_status"] = "proposed"
        if not text:
            text = "Предлагаю план правок — подтверди выполнение."
    msg = ChatMessage(job_id=job.id, role="agent", text=text, extra=extra)
    session.add(msg)
    await session.flush()
    await emit(session, "chat", job=job, payload={
        "message_id": msg.id, "role": "agent", "has_plan": bool(plan),
    })
    return f"agent reply {msg.id}, plan={len(plan)} steps"


# ------------------------------------------------------------------ apply

def _overrides_path(job: VideoJob, name: str):
    d = job_dir(job.batch_id, job.id, "overrides")
    d.mkdir(parents=True, exist_ok=True)
    return d / name


async def _new_script_version(
    session: AsyncSession, job: VideoJob, draft: ScriptDraft, note: str
) -> Script:
    nv = (
        (await session.execute(select(func.max(Script.version)).where(Script.job_id == job.id))).scalar() or 0
    ) + 1
    script = Script(
        job_id=job.id, version=nv, title=draft.title, description=draft.description,
        hook_pattern=draft.hook_pattern, notes=note,
    )
    session.add(script)
    await session.flush()
    for b in draft.blocks:
        session.add(Block(
            script_id=script.id, ordinal=int(b.get("ordinal", 0)),
            role=str(b.get("role", "")), text_en=str(b.get("text_en", "")),
            frame_desc=str(b.get("frame_desc", "")),
            search_keys=list(b.get("search_keys", [])), fx=list(b.get("fx", [])),
        ))
    await session.flush()
    return script


async def _current_draft(session: AsyncSession, job: VideoJob) -> ScriptDraft:
    script = await active_script(session, job)
    if script is None:
        raise RuntimeError("нет сценария для правки")
    blocks = await script_blocks(session, script)
    return ScriptDraft(
        title=script.title, description=script.description,
        hook_pattern=script.hook_pattern,
        blocks=[{
            "ordinal": b.ordinal, "role": b.role, "text_en": b.text_en,
            "frame_desc": b.frame_desc, "search_keys": b.search_keys, "fx": b.fx,
        } for b in blocks],
    )


async def run_apply_plan(
    session: AsyncSession, job: VideoJob, message_id: str, ctx: dict | None
) -> str:
    from . import flow  # локальный импорт против циклов

    msg = await session.get(ChatMessage, message_id)
    if msg is None or not (msg.extra or {}).get("plan"):
        raise RuntimeError("план не найден")
    plan = msg.extra["plan"]
    providers = await get_providers(session)

    flags = {"script": False, "voice": False, "render": False}
    force_step: str | None = None
    done_notes: list[str] = []

    for step in plan:
        tool, args = step.get("tool"), step.get("args", {})
        if tool in ("edit_script", "retime"):
            instruction = args.get("instruction", "")
            if tool == "retime":
                target = float(args.get("target_seconds", 30))
                words = int(target * 2.8)
                instruction = (
                    f"Rewrite to fit ~{target:.0f} seconds total (~{words} words), "
                    "keep the hook and the payoff."
                )
            draft = await providers.llm.edit_script(
                await _current_draft(session, job),
                instruction=instruction,
                block_ordinal=args.get("block_ordinal"),
            )
            await _new_script_version(session, job, draft, note=f"chat: {instruction[:200]}")
            flags["script"] = True
            done_notes.append(f"сценарий переписан ({tool})")
        elif tool == "replace_clip":
            script = await active_script(session, job)
            blocks = await script_blocks(session, script)
            block = next((b for b in blocks if b.ordinal == int(args.get("block_ordinal", 0))), None)
            if block is None:
                done_notes.append(f"replace_clip: блок {args.get('block_ordinal')} не найден")
                continue
            cands = sorted(
                (await session.execute(
                    select(ClipCandidate).where(ClipCandidate.block_id == block.id)
                )).scalars().all(),
                key=lambda c: c.rank,
            )
            if not cands:
                done_notes.append(f"replace_clip: у блока {block.ordinal} нет кандидатов")
                continue
            want = args.get("candidate_rank")
            cur = next((i for i, c in enumerate(cands) if c.chosen), -1)
            new = (
                next((c for c in cands if c.rank == int(want)), None)
                if want else cands[(cur + 1) % len(cands)]
            )
            if new is None:
                done_notes.append(f"replace_clip: rank {want} не найден")
                continue
            for c in cands:
                c.chosen = c.id == new.id
            flags["render"] = True
            done_notes.append(f"блок {block.ordinal}: клип -> кандидат rank={new.rank}")
        elif tool == "add_candidates":
            script = await active_script(session, job)
            blocks = await script_blocks(session, script)
            block = next((b for b in blocks if b.ordinal == int(args.get("block_ordinal", 0))), None)
            if block is None:
                done_notes.append("add_candidates: блок не найден")
                continue
            detail = await cutter.add_extra_candidates(
                session, job, block.id,
                query=args.get("query"), yt_url=args.get("yt_url"),
            )
            done_notes.append(detail)
        elif tool == "regen_voice":
            flags["voice"] = True
            done_notes.append("переозвучка запрошена")
        elif tool == "restyle_subs":
            p = _overrides_path(job, "subs_dictionary.json")
            cur = json.loads(p.read_text()) if p.exists() else {}
            cur.update(args.get("replacements") or {})
            if args.get("note"):
                cur.setdefault("__notes__", []).append(args["note"])
            p.write_text(json.dumps(cur, ensure_ascii=False, indent=1))
            flags["render"] = True
            done_notes.append("сабы: словарь обновлён")
        elif tool == "add_sfx":
            script = await active_script(session, job)
            blocks = await script_blocks(session, script)
            block = next((b for b in blocks if b.ordinal == int(args.get("block_ordinal", 0))), None)
            if block is not None:
                block.fx = list(block.fx or []) + [{"t": "sfx", "name": args.get("name", "impact")}]
                flags["render"] = True
                done_notes.append(f"блок {block.ordinal}: +sfx {args.get('name')}")
        elif tool == "set_music":
            p = _overrides_path(job, "music.json")
            cur = json.loads(p.read_text()) if p.exists() else {}
            if args.get("volume") is not None:
                cur["volume"] = max(0.0, min(1.0, float(args["volume"])))
            if args.get("mood"):
                cur["mood"] = args["mood"]
            p.write_text(json.dumps(cur, ensure_ascii=False))
            flags["render"] = True
            done_notes.append(f"музыка: {cur}")
        elif tool == "rerender":
            force_step = args.get("step", "master")
            done_notes.append(f"пересборка {force_step}")
        else:
            done_notes.append(f"неизвестный инструмент {tool!r} — пропущен")

    # --- маршрутизация перезапуска
    enq: str | None = None
    st = job.status
    if force_step == "rough":
        enq = "run_rough"
    elif force_step == "master":
        enq = "run_master"
    elif flags["script"]:
        if st == JobStatus.GATE_SCRIPT:
            enq = None  # остаёмся на гейте, сценарий обновился
        elif st == JobStatus.GATE_CLIPS:
            enq = "run_hunter"  # новые ключи -> новая охота
        else:
            enq = "run_voicer"  # переозвучка -> черновик -> гейт G3
    elif flags["voice"]:
        enq = "run_voicer"
    elif flags["render"]:
        if st in (JobStatus.GATE_MASTER, JobStatus.DONE):
            enq = "run_master"
        elif st == JobStatus.GATE_ROUGH:
            enq = "run_rough"
        elif st == JobStatus.GATE_CLIPS:
            enq = None  # выбор клипов виден сразу на гейте

    msg.extra = {**msg.extra, "plan_status": "executed"}
    summary = "Выполнено:\n- " + "\n- ".join(done_notes)
    if enq:
        summary += f"\nПерезапускаю: {enq.replace('run_', '')}"
        gate_map = {
            "run_hunter": GateType.CLIPS, "run_voicer": GateType.ROUGH,
            "run_rough": GateType.ROUGH, "run_master": GateType.MASTER,
        }
        g = gate_map.get(enq)
        if g:
            gates = await flow.ensure_gates(session, job)
            if g in gates:
                gates[g].status = GateStatus.REWORK
    out = ChatMessage(job_id=job.id, role="system", text=summary, extra={"applied_plan": message_id})
    session.add(out)
    await session.flush()
    await emit(session, "chat", job=job, payload={"message_id": out.id, "role": "system"})

    if enq and ctx and ctx.get("redis"):
        await ctx["redis"].enqueue_job(enq, job.id)
    return f"plan applied: {len(plan)} steps, rerun={enq or 'none'}"
