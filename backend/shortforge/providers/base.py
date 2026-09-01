"""Интерфейсы провайдеров (ADR-011). Реализации: providers/real.py и providers/mock.py.

Фабрика get_providers(session) читает settings из БД:
- есть anthropic_api_key -> RealLLM, иначе MockLLM
- есть elevenlabs_api_key -> RealTTS, иначе MockTTS
- youtube: real если settings.ytdlp_enabled != "0" и yt-dlp доступен; mock иначе
Провайдеры создаются на каждый запуск задачи — смена ключей не требует рестарта (ADR-009).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ScriptDraft:
    title: str
    description: str
    hook_pattern: str
    blocks: list[dict]  # {ordinal, role, text_en, frame_desc, search_keys[], fx[]}


@dataclass
class FoundVideo:
    yt_video_id: str
    title: str
    channel: str
    duration: float
    transcript: list[dict] = field(default_factory=list)  # [{"t":sec,"s":text}]


@dataclass
class TTSResult:
    wav_path: str
    words: list[dict]  # [{"w":word,"s":sec,"e":sec}]
    duration: float
    is_mock: bool


class LLMProvider(Protocol):
    is_mock: bool

    async def write_script(self, *, game: str, idea: str, fmt: str, context: str) -> ScriptDraft: ...
    async def punch_up(self, draft: ScriptDraft, *, game: str, fmt: str) -> ScriptDraft: ...
    async def edit_script(self, current: ScriptDraft, *, instruction: str, block_ordinal: int | None) -> ScriptDraft: ...
    async def agent_turn(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        """Возвращает сырое сообщение Anthropic-формата: {stop_reason, content:[...]}.
        Mock: правило-ориентированный разбор инструкции в план инструментов."""
        ...


class TTSProvider(Protocol):
    is_mock: bool

    async def synth(self, *, text: str, voice_id: str, out_wav: str) -> TTSResult: ...


class YouTubeProvider(Protocol):
    is_mock: bool

    async def search(self, *, query: str, limit: int) -> list[FoundVideo]: ...
    async def download_window(self, *, yt_video_id: str, start: float, end: float, out_path: str) -> str:
        """Скачивает окно [start,end] в out_path (mp4, 1080p max). Возвращает путь."""
        ...
    async def get_transcript(self, *, yt_video_id: str) -> list[dict]: ...
