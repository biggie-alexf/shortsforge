"""`python -m shortforge.db_init` — создаёт таблицы (create_all).

Gate-строки здесь НЕ создаются: 4 гейта на job создаёт API при создании батча.
"""
from __future__ import annotations

import asyncio

from .db import dispose_engine, get_engine
from .models import Base


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def main() -> None:
    asyncio.run(_main())


async def _main() -> None:
    await init_db()
    print("db_init: tables created (create_all).")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
