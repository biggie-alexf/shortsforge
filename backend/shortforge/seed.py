"""`python -m shortforge.seed` — создаёт пользователя admin/admin и ChannelPreset "main"."""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from .db import dispose_engine, get_sessionmaker
from .models import ChannelPreset, User
from .security import hash_password

ADMIN_LOGIN = "admin"
ADMIN_PASSWORD = "admin"


async def seed() -> None:
    async with get_sessionmaker()() as session:
        user = (
            await session.execute(select(User).where(User.login == ADMIN_LOGIN))
        ).scalar_one_or_none()
        if user is None:
            session.add(User(login=ADMIN_LOGIN, pw_hash=hash_password(ADMIN_PASSWORD)))
            print(f"seed: created user {ADMIN_LOGIN} / {ADMIN_PASSWORD}")
        else:
            print(f"seed: user {ADMIN_LOGIN} already exists (password unchanged)")

        preset = (
            await session.execute(
                select(ChannelPreset).where(ChannelPreset.name == "main")
            )
        ).scalar_one_or_none()
        if preset is None:
            session.add(ChannelPreset(name="main"))
            print('seed: created ChannelPreset "main"')
        else:
            print('seed: ChannelPreset "main" already exists')

        await session.commit()
    print(f"seed: credentials -> login={ADMIN_LOGIN} password={ADMIN_PASSWORD}")


async def _main() -> None:
    await seed()
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
