"""Settings: шифрование секретов, маскирование, providers-статус."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from shortforge.db import get_sessionmaker
from shortforge.models import Setting
from shortforge.settings_store import decrypt_value

pytestmark = pytest.mark.asyncio


async def test_settings_require_auth(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 401


async def test_put_secret_masked_and_encrypted(auth_client):
    client = auth_client
    resp = await client.put(
        "/api/settings/anthropic_api_key", json={"value": "sk-ant-secret-abcd"}
    )
    assert resp.status_code == 200

    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    rows = {r["key"]: r for r in resp.json()}
    row = rows["anthropic_api_key"]
    assert row["secret"] is True
    assert row["value_masked"] == "****abcd"
    assert "sk-ant" not in row["value_masked"]
    assert row["updated_at"] is not None

    # в БД лежит шифртекст, который расшифровывается обратно
    async with get_sessionmaker()() as s:
        db_row = (
            await s.execute(select(Setting).where(Setting.key == "anthropic_api_key"))
        ).scalar_one()
        assert db_row.value != "sk-ant-secret-abcd"
        assert decrypt_value(db_row.value) == "sk-ant-secret-abcd"


async def test_put_non_secret_plain(auth_client):
    client = auth_client
    resp = await client.put(
        "/api/settings/anthropic_model", json={"value": "claude-sonnet-4-5"}
    )
    assert resp.status_code == 200
    resp = await client.get("/api/settings")
    rows = {r["key"]: r for r in resp.json()}
    assert rows["anthropic_model"]["value_masked"] == "claude-sonnet-4-5"
    assert rows["anthropic_model"]["secret"] is False

    async with get_sessionmaker()() as s:
        db_row = (
            await s.execute(select(Setting).where(Setting.key == "anthropic_model"))
        ).scalar_one()
        assert db_row.value == "claude-sonnet-4-5"


async def test_all_known_keys_listed(auth_client):
    resp = await auth_client.get("/api/settings")
    keys = {r["key"] for r in resp.json()}
    assert keys == {
        "anthropic_api_key", "anthropic_model", "elevenlabs_api_key",
        "elevenlabs_voice_id", "ytdlp_proxy", "ytdlp_cookies",
        "channel_watermark", "subs_dictionary",
    }
    # неустановленный секрет маскируется в пустую строку
    rows = {r["key"]: r for r in resp.json()}
    assert rows["elevenlabs_api_key"]["value_masked"] == ""


async def test_unknown_key_rejected(auth_client):
    resp = await auth_client.put("/api/settings/evil_key", json={"value": "x"})
    assert resp.status_code == 400
    assert "error" in resp.json()


async def test_providers_status(auth_client):
    client = auth_client
    resp = await client.get("/api/settings/providers")
    assert resp.status_code == 200
    status = resp.json()
    # yt_dlp установлен в окружении -> youtube real; ключей нет -> llm/tts mock
    assert status == {"llm": "mock", "tts": "mock", "youtube": "real"}

    await client.put("/api/settings/anthropic_api_key", json={"value": "sk-ant-1234"})
    await client.put("/api/settings/elevenlabs_api_key", json={"value": "el-5678"})
    resp = await client.get("/api/settings/providers")
    status = resp.json()
    assert status["llm"] == "real"
    assert status["tts"] == "real"
