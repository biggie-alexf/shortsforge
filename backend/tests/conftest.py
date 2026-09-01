"""Общий conftest: тестовая БД shortforge_test + фикстуры API-тестов и пайплайна.

- test_engine: пересоздаёт схему, подменяет глобальный engine на NullPool
  (у каждого теста свой event loop — пул соединений между ними жить не может).
- client / auth_client / admin: httpx-клиент поверх ASGI-приложения,
  queue.enqueue подменён на запись в client.enqueued: (task, args, kwargs).
- fixture_videos: синтетические летсплеи для медиа-тестов (кэш в DATA_DIR/_fixtures).
"""
from __future__ import annotations

import asyncio
import os

os.environ.setdefault("APP_SECRET", "dev-secret")
os.environ.setdefault("DATA_DIR", "/home/user/shortforge-data")
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://shortforge:shortforge@127.0.0.1:5432/shortforge_test",
)
os.environ["DATABASE_URL"] = TEST_DB

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from shortforge import db as sfdb  # noqa: E402
from shortforge.models import Base, User  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def test_engine():
    engine = create_async_engine(TEST_DB, poolclass=NullPool)
    sfdb._engine = engine
    sfdb._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def _prepare():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_prepare())
    yield engine
    asyncio.run(engine.dispose())
    sfdb._engine = None
    sfdb._sessionmaker = None


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(test_engine):
    """Изоляция тестов: чистим все таблицы перед каждым тестом."""
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    yield


# ------------------------------------------------------------------ API-фикстуры

@pytest_asyncio.fixture
async def client(monkeypatch):
    """ASGI-клиент; enqueue пишется в client.enqueued вместо Redis."""
    import httpx

    from shortforge.api import queue
    from shortforge.api.app import app

    enqueued: list[tuple] = []

    async def fake_enqueue(task_name, *args, **kwargs):
        enqueued.append((task_name, args, kwargs))

    monkeypatch.setattr(queue, "enqueue", fake_enqueue)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.enqueued = enqueued
        yield c


@pytest_asyncio.fixture
async def admin():
    """Пользователь admin/admin (get-or-create)."""
    from shortforge.security import hash_password

    async with sfdb.get_sessionmaker()() as s:
        user = (
            await s.execute(select(User).where(User.login == "admin"))
        ).scalar_one_or_none()
        if user is None:
            user = User(login="admin", pw_hash=hash_password("admin"))
            s.add(user)
            await s.commit()
        return user


@pytest_asyncio.fixture
async def auth_client(client, admin):
    resp = await client.post(
        "/api/auth/login", json={"login": "admin", "password": "admin"}
    )
    assert resp.status_code == 200, resp.text
    return client


# ------------------------------------------------------------------ медиа-фикстуры

@pytest.fixture(scope="session")
def fixture_videos():
    """Синтетические летсплеи (генерятся один раз, кэшируются в DATA_DIR/_fixtures)."""
    from shortforge.media.fixtures import ensure_fixtures

    return ensure_fixtures()
