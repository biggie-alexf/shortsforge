"""ShortForge — SQLAlchemy models. ЕДИНЫЙ КОНТРАКТ ДАННЫХ.

Агенты: не переименовывать таблицы/колонки без обновления docs/api-spec.md.
Статусы и enum-ы — единственный источник правды здесь.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def uid() -> str:
    return uuid.uuid4().hex[:12]


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- enums

class VideoFormat(str, enum.Enum):
    A = "A"  # секрет/разбор
    B = "B"  # мемный монолог
    C = "C"  # скетч (зарезервирован)


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    SCRIPTING = "scripting"
    GATE_SCRIPT = "gate_script"        # G1
    HUNTING = "hunting"
    CUTTING = "cutting"
    GATE_CLIPS = "gate_clips"          # G2
    VOICING = "voicing"
    ROUGH_RENDER = "rough_render"
    GATE_ROUGH = "gate_rough"          # G3
    MASTER_RENDER = "master_render"
    GATE_MASTER = "gate_master"        # G4
    DONE = "done"
    FAILED = "failed"


class GateType(str, enum.Enum):
    SCRIPT = "script"
    CLIPS = "clips"
    ROUGH = "rough"
    MASTER = "master"


class GateStatus(str, enum.Enum):
    PENDING = "pending"      # ещё не дошли
    OPEN = "open"            # ждёт решения человека
    APPROVED = "approved"
    REWORK = "rework"        # отправлено на переделку (правки применяются)


class BlockStatus(str, enum.Enum):
    OK = "ok"
    NEEDS_FOOTAGE = "needs_footage"


class StepName(str, enum.Enum):
    WRITER = "writer"
    HUNTER = "hunter"
    CUTTER = "cutter"
    VOICER = "voicer"
    ROUGH_MIXER = "rough_mixer"
    MASTER_MIXER = "master_mixer"


# ---------------------------------------------------------------- core

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uid)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    pw_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Setting(Base):
    """Ключи и конфиг провайдеров. value шифруется Fernet(APP_SECRET) если secret=True."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # известные ключи: anthropic_api_key, anthropic_model, elevenlabs_api_key,
    # elevenlabs_voice_id, ytdlp_proxy, ytdlp_cookies (текст cookies.txt),
    # channel_watermark, subs_dictionary (JSON: замены слов в сабах)


class ChannelPreset(Base):
    __tablename__ = "channel_presets"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(64), default="main")
    voice_id: Mapped[str] = mapped_column(String(64), default="")
    ass_template: Mapped[str] = mapped_column(Text, default="")  # пусто = дефолтный шаблон из кода
    watermark_text: Mapped[str] = mapped_column(String(64), default="")
    default_format: Mapped[VideoFormat] = mapped_column(Enum(VideoFormat), default=VideoFormat.A)


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(128), default="")
    channel_preset_id: Mapped[str | None] = mapped_column(ForeignKey("channel_presets.id"), nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    jobs: Mapped[list[VideoJob]] = relationship(back_populates="batch", order_by="VideoJob.created_at")


class VideoJob(Base):
    __tablename__ = "video_jobs"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uid)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"))
    game: Mapped[str] = mapped_column(String(128))            # напр. "Steal a Brainrot"
    idea: Mapped[str] = mapped_column(Text)                    # краткая идея от заказчика
    format: Mapped[VideoFormat] = mapped_column(Enum(VideoFormat), default=VideoFormat.A)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.QUEUED)
    error: Mapped[str] = mapped_column(Text, default="")       # текст последней ошибки (status=FAILED)
    current_version: Mapped[int] = mapped_column(Integer, default=0)  # номер последнего мастера
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    batch: Mapped[Batch] = relationship(back_populates="jobs")
    gates: Mapped[list[Gate]] = relationship(back_populates="job", order_by="Gate.ordinal")
    scripts: Mapped[list[Script]] = relationship(back_populates="job", order_by="Script.version")
    renders: Mapped[list[Render]] = relationship(back_populates="job", order_by="Render.created_at")
    messages: Mapped[list[ChatMessage]] = relationship(back_populates="job", order_by="ChatMessage.created_at")

    # data-директория: {DATA_DIR}/{batch_id}/{job_id}/...


class Gate(Base):
    __tablename__ = "gates"
    __table_args__ = (UniqueConstraint("job_id", "type"),)
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("video_jobs.id"))
    type: Mapped[GateType] = mapped_column(Enum(GateType))
    ordinal: Mapped[int] = mapped_column(Integer)  # 1..4
    status: Mapped[GateStatus] = mapped_column(Enum(GateStatus), default=GateStatus.PENDING)
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False)  # ADR-002: заложено, выключено
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    job: Mapped[VideoJob] = relationship(back_populates="gates")


class Script(Base):
    """Версионируемый сценарий. Активная версия = max(version)."""
    __tablename__ = "scripts"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("video_jobs.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(256), default="")       # заголовок ролика (EN)
    description: Mapped[str] = mapped_column(Text, default="")        # описание+хэштеги (EN)
    hook_pattern: Mapped[str] = mapped_column(String(64), default="") # какой хук-паттерн использован
    notes: Mapped[str] = mapped_column(Text, default="")              # каким промптом/правкой получен
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    job: Mapped[VideoJob] = relationship(back_populates="scripts")
    blocks: Mapped[list[Block]] = relationship(back_populates="script", order_by="Block.ordinal")


class Block(Base):
    __tablename__ = "blocks"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uid)
    script_id: Mapped[str] = mapped_column(ForeignKey("scripts.id"))
    ordinal: Mapped[int] = mapped_column(Integer)                 # 1..N
    role: Mapped[str] = mapped_column(String(32), default="")     # hook|setup|evidence|cta|twist|loop|punch
    text_en: Mapped[str] = mapped_column(Text)                    # текст озвучки блока
    frame_desc: Mapped[str] = mapped_column(Text, default="")     # что должно быть в кадре
    search_keys: Mapped[list] = mapped_column(JSON, default=list) # ключи поиска доноров
    fx: Mapped[list] = mapped_column(JSON, default=list)          # метки: [{"t":"zoom","target":"badge"},{"t":"sfx","name":"impact"}]
    status: Mapped[BlockStatus] = mapped_column(Enum(BlockStatus), default=BlockStatus.OK)
    # тайминги заполняет voicer:
    t_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    t_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    script: Mapped[Script] = relationship(back_populates="blocks")
    candidates: Mapped[list[ClipCandidate]] = relationship(back_populates="block", order_by="ClipCandidate.rank")


class Donor(Base):
    __tablename__ = "donors"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("video_jobs.id"))
    yt_video_id: Mapped[str] = mapped_column(String(24))
    yt_channel: Mapped[str] = mapped_column(String(128), default="")
    yt_title: Mapped[str] = mapped_column(String(256), default="")
    window_start: Mapped[float] = mapped_column(Float, default=0)   # сек в исходнике
    window_end: Mapped[float] = mapped_column(Float, default=0)
    file_path: Mapped[str] = mapped_column(String(512), default="")  # скачанное окно
    transcript: Mapped[list] = mapped_column(JSON, default=list)     # [{"t":сек,"s":текст}]
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClipCandidate(Base):
    __tablename__ = "clip_candidates"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uid)
    block_id: Mapped[str] = mapped_column(ForeignKey("blocks.id"))
    donor_id: Mapped[str | None] = mapped_column(ForeignKey("donors.id"), nullable=True)
    rank: Mapped[int] = mapped_column(Integer, default=1)          # 1..3
    file_path: Mapped[str] = mapped_column(String(512))            # вертикальный сниппет mp4 (без звука)
    src_start: Mapped[float] = mapped_column(Float, default=0)     # сек в доноре
    duration: Mapped[float] = mapped_column(Float, default=0)
    motion_score: Mapped[float] = mapped_column(Float, default=0)
    chosen: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_note: Mapped[str] = mapped_column(String(256), default="")  # если добавлен руками/чатом
    block: Mapped[Block] = relationship(back_populates="candidates")


class VoiceTrack(Base):
    __tablename__ = "voice_tracks"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("video_jobs.id"))
    script_version: Mapped[int] = mapped_column(Integer)
    wav_path: Mapped[str] = mapped_column(String(512))
    words: Mapped[list] = mapped_column(JSON, default=list)  # [{"w":"This","s":0.00,"e":0.18}]
    duration: Mapped[float] = mapped_column(Float, default=0)
    voice_id: Mapped[str] = mapped_column(String(64), default="")
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Render(Base):
    """rough или master; master версионируется (version>=1)."""
    __tablename__ = "renders"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("video_jobs.id"))
    kind: Mapped[str] = mapped_column(String(16))                  # "rough" | "master"
    version: Mapped[int] = mapped_column(Integer, default=1)
    file_path: Mapped[str] = mapped_column(String(512))
    preview_path: Mapped[str] = mapped_column(String(512), default="")   # jpg первого кадра
    changelog: Mapped[str] = mapped_column(Text, default="")             # что изменилось vs прошлой версии
    qc: Mapped[dict] = mapped_column(JSON, default=dict)                 # {"lufs":-14.1,"duration":34.2,...}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    job: Mapped[VideoJob] = relationship(back_populates="renders")


class ChatMessage(Base):
    """Чат видео. role: user|agent|system. Для agent: plan/tool_calls в extra."""
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("video_jobs.id"))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    # extra для agent-плана: {"plan":[{"tool":"replace_clip","args":{...},"why":"..."}],
    #                         "plan_status":"proposed|confirmed|executed|rejected"}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    job: Mapped[VideoJob] = relationship(back_populates="messages")


class StepRun(Base):
    """Журнал запусков шагов пайплайна (ретраи, тайминги, ошибки)."""
    __tablename__ = "step_runs"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("video_jobs.id"))
    step: Mapped[StepName] = mapped_column(Enum(StepName))
    ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # None = выполняется
    detail: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Event(Base):
    """Лента событий для SSE (фронт подписывается на /api/events)."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    type: Mapped[str] = mapped_column(String(48))   # job_status|gate_open|render_ready|chat|needs_footage|error
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
