"""Auth: login/logout/me, кука sf_session, users CRUD, /media под сессией."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_me_unauthenticated(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
    assert "error" in resp.json()


async def test_login_wrong_credentials(client, admin):
    resp = await client.post(
        "/api/auth/login", json={"login": "admin", "password": "nope"}
    )
    assert resp.status_code == 401
    assert resp.json() == {"error": "invalid credentials"}

    resp = await client.post(
        "/api/auth/login", json={"login": "ghost", "password": "admin"}
    )
    assert resp.status_code == 401


async def test_login_me_logout(client, admin):
    resp = await client.post(
        "/api/auth/login", json={"login": "admin", "password": "admin"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["login"] == "admin"
    assert "sf_session" in resp.cookies

    resp = await client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user"]["login"] == "admin"

    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 200

    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_media_requires_session(client, admin):
    resp = await client.get("/media/some/file.mp4")
    assert resp.status_code == 401

    await client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
    resp = await client.get("/media/some/file.mp4")
    assert resp.status_code == 404  # авторизован, но файла нет


async def test_users_crud(auth_client):
    client = auth_client

    resp = await client.post(
        "/api/users", json={"login": "editor", "password": "secret123"}
    )
    assert resp.status_code == 200
    editor_id = resp.json()["id"]

    # дубликат логина
    resp = await client.post(
        "/api/users", json={"login": "editor", "password": "x"}
    )
    assert resp.status_code == 409

    resp = await client.get("/api/users")
    assert resp.status_code == 200
    logins = [u["login"] for u in resp.json()]
    assert "admin" in logins and "editor" in logins

    # новый пользователь может логиниться
    resp = await client.post(
        "/api/auth/login", json={"login": "editor", "password": "secret123"}
    )
    assert resp.status_code == 200

    # вернёмся под admin и удалим editor
    await client.post("/api/auth/login", json={"login": "admin", "password": "admin"})
    resp = await client.delete(f"/api/users/{editor_id}")
    assert resp.status_code == 200

    resp = await client.delete(f"/api/users/{editor_id}")
    assert resp.status_code == 404


async def test_cannot_delete_self(auth_client, admin):
    resp = await auth_client.delete(f"/api/users/{admin.id}")
    assert resp.status_code == 400
