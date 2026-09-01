"""arq-очередь: enqueue задач по имени строки (воркер живёт в pipeline/, сюда не импортируется).

В тестах `enqueue` monkeypatch-ится на no-op.
"""
from __future__ import annotations

import os
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

_pool: ArqRedis | None = None


def redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(redis_url()))
    return _pool


async def enqueue(task_name: str, *args: Any, **kwargs: Any) -> None:
    """enqueue_job по имени строки, напр. enqueue("run_writer", job_id)."""
    pool = await get_pool()
    await pool.enqueue_job(task_name, *args, **kwargs)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
    _pool = None
