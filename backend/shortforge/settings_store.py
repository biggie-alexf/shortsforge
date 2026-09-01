"""Настройки в таблице settings (ADR-009).

Секретные значения шифруются Fernet-ключом, производным от APP_SECRET
(sha256 -> urlsafe base64). Провайдеры читают настройки на каждый запуск задачи.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Setting

# key -> secret?
KNOWN_SETTINGS: dict[str, bool] = {
    "anthropic_api_key": True,
    "anthropic_model": False,
    "elevenlabs_api_key": True,
    "elevenlabs_voice_id": False,
    "ytdlp_proxy": True,
    "ytdlp_cookies": True,
    "channel_watermark": False,
    "subs_dictionary": False,
}


def fernet() -> Fernet:
    secret = os.environ.get("APP_SECRET", "dev-secret")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_value(value: str) -> str:
    return fernet().encrypt(value.encode()).decode()


def decrypt_value(token: str) -> str:
    if not token:
        return ""
    try:
        return fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""


async def get_setting(session: AsyncSession, key: str, default: str = "") -> str:
    row = (
        await session.execute(select(Setting).where(Setting.key == key))
    ).scalar_one_or_none()
    if row is None or not row.value:
        return default
    return decrypt_value(row.value) if row.secret else row.value


async def set_setting(session: AsyncSession, key: str, value: str) -> Setting:
    if key not in KNOWN_SETTINGS:
        raise KeyError(key)
    secret = KNOWN_SETTINGS[key]
    stored = encrypt_value(value) if (secret and value) else value
    row = (
        await session.execute(select(Setting).where(Setting.key == key))
    ).scalar_one_or_none()
    if row is None:
        row = Setting(key=key, value=stored, secret=secret)
        session.add(row)
    else:
        row.value = stored
        row.secret = secret
    await session.flush()
    return row


async def list_settings(session: AsyncSession) -> list[dict]:
    """Все известные ключи; секреты маскируются (****+последние 4)."""
    rows = {
        r.key: r
        for r in (await session.execute(select(Setting))).scalars()
        if r.key in KNOWN_SETTINGS
    }
    out = []
    for key, secret in KNOWN_SETTINGS.items():
        row = rows.get(key)
        plain = ""
        if row is not None and row.value:
            plain = decrypt_value(row.value) if row.secret else row.value
        if secret:
            masked = ("****" + plain[-4:]) if plain else ""
        else:
            masked = plain
        out.append(
            {
                "key": key,
                "value_masked": masked,
                "secret": secret,
                "updated_at": row.updated_at.isoformat() if row is not None else None,
            }
        )
    return out


async def providers_status(session: AsyncSession) -> dict[str, str]:
    """{"llm","tts","youtube"} -> "real" | "mock" по наличию ключей."""
    llm = "real" if await get_setting(session, "anthropic_api_key") else "mock"
    tts = "real" if await get_setting(session, "elevenlabs_api_key") else "mock"
    youtube = "mock"
    try:
        import yt_dlp  # noqa: F401

        youtube = "real"
    except ImportError:
        pass
    if await get_setting(session, "ytdlp_proxy"):
        youtube = "real"
    return {"llm": llm, "tts": tts, "youtube": youtube}
