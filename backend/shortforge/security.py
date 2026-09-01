"""Пароли (bcrypt) и сессии (itsdangerous signed cookie `sf_session`, TTL 30 дней).

Серверного стора сессий нет: кука подписана APP_SECRET и содержит user_id.
"""
from __future__ import annotations

import os

import bcrypt
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import User

SESSION_COOKIE = "sf_session"
SESSION_TTL = 30 * 24 * 3600  # 30 дней


def app_secret() -> str:
    return os.environ.get("APP_SECRET", "dev-secret")


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(app_secret(), salt="sf_session")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, pw_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), pw_hash.encode())
    except ValueError:
        return False


def make_session_cookie(user_id: str) -> str:
    return _serializer().dumps({"uid": user_id})


def read_session_cookie(value: str | None) -> str | None:
    """Вернёт user_id или None (нет куки / подпись не сошлась / истекла)."""
    if not value:
        return None
    try:
        data = _serializer().loads(value, max_age=SESSION_TTL)
    except (BadSignature, SignatureExpired):
        return None
    if isinstance(data, dict):
        return data.get("uid")
    return None


async def current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    """FastAPI dependency: пользователь из куки sf_session, иначе 401."""
    uid = read_session_cookie(request.cookies.get(SESSION_COOKIE))
    if uid is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user
