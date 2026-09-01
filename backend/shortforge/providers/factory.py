"""Фабрика провайдеров (ADR-009/011): выбор real/mock по settings из БД.

Правила (docstring base.py):
- anthropic_api_key задан      -> RealLLM, иначе MockLLM
- elevenlabs_api_key задан     -> RealTTS, иначе MockTTS
- youtube: real если settings.ytdlp_enabled != "0" и yt-dlp импортируется; mock иначе
  (по умолчанию ytdlp_enabled считается "0" — mock-флоу работает без сети из коробки)

Провайдеры создаются на каждый запуск задачи — смена ключей не требует рестарта.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..settings_store import get_setting
from .base import LLMProvider, TTSProvider, YouTubeProvider
from .mock import MockLLM, MockTTS, MockYouTube
from .real import RealLLM, RealTTS, RealYouTube


@dataclass
class Providers:
    llm: LLMProvider
    tts: TTSProvider
    youtube: YouTubeProvider
    voice_id: str = ""


async def get_providers(session: AsyncSession) -> Providers:
    anthropic_key = await get_setting(session, "anthropic_api_key")
    anthropic_model = await get_setting(session, "anthropic_model")
    eleven_key = await get_setting(session, "elevenlabs_api_key")
    voice_id = await get_setting(session, "elevenlabs_voice_id")
    ytdlp_enabled = await get_setting(session, "ytdlp_enabled", "0")
    ytdlp_proxy = await get_setting(session, "ytdlp_proxy")
    ytdlp_cookies = await get_setting(session, "ytdlp_cookies")

    llm: LLMProvider = (
        RealLLM(api_key=anthropic_key, model=anthropic_model)
        if anthropic_key else MockLLM()
    )
    tts: TTSProvider = RealTTS(api_key=eleven_key) if eleven_key else MockTTS()

    youtube: YouTubeProvider = MockYouTube()
    if ytdlp_enabled != "0":
        try:
            import yt_dlp  # noqa: F401, PLC0415

            youtube = RealYouTube(proxy=ytdlp_proxy, cookies=ytdlp_cookies)
        except ImportError:
            pass

    return Providers(llm=llm, tts=tts, youtube=youtube, voice_id=voice_id)
