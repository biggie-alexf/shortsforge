# ShortForge — конвенции проекта (читать каждому агенту)

Сервис: батч «3 Roblox-игры + идеи» → 3 вертикальных видео через пайплайн с 4 ручными гейтами
и чатом-правок. Документация: docs/adr/*.md (решения), docs/glossary.md (термины),
docs/api-spec.md (КОНТРАКТ API — не менять без правки самого файла),
backend/shortforge/models.py (КОНТРАКТ ДАННЫХ — единственный источник статусов/enum).
Производственные правила видео: docs/bible-summary.md.

## Структура
- backend/  — Python 3.11+, пакет `shortforge`. FastAPI (api/), пайплайн (pipeline/),
  провайдеры (providers/), медиа-утилиты (media/). Тесты в backend/tests (pytest, pytest-asyncio).
- frontend/ — React 18 + Vite + TypeScript, ТОЛЬКО тёмная тема (ADR-010). Без UI-китов,
  свой CSS (styles.css, дизайн-токены). fetch на /api (vite proxy → :8000).
- deploy/   — docker-compose.yml, Dockerfile.api, Dockerfile.front, Caddyfile, deploy.sh.
- scripts/  — dev-скрипты (make_fixtures.py, seed.py, dev_run.sh).

## Дев-окружение (в этом контейнере НЕТ Docker)
- Postgres 16 локально: кластер main на :5432, БД shortforge, юзер shortforge/shortforge.
- Redis локально на :6379.
- `make dev-api` (uvicorn :8000), `make dev-worker` (arq), `make dev-front` (vite :5173).
- env: DATABASE_URL=postgresql+asyncpg://shortforge:shortforge@127.0.0.1:5432/shortforge
  REDIS_URL=redis://127.0.0.1:6379/0  APP_SECRET=dev-secret  DATA_DIR=/home/user/shortforge-data
- Python-зависимости ставить `pip install --break-system-packages`, и ОБЯЗАТЕЛЬНО дописывать
  в backend/requirements.txt с закреплённой версией.

## Правила кода
- Async everywhere (SQLAlchemy async + asyncpg, httpx). Форматирование: black по умолчанию, не спорим.
- Никаких API-ключей в коде/env: ключи в таблице settings (Fernet, ADR-009).
- Каждый внешний вызов — через провайдера с mock-реализацией (ADR-011). Mock обязан работать
  без сети и без ключей.
- ffmpeg вызывать subprocess-ом с явными аргументами (никаких shell=True), логировать команду.
- Пути к артефактам: {DATA_DIR}/{batch_id}/{job_id}/ (donors/, candidates/, voice/, renders/).
  В БД хранить пути относительно DATA_DIR.
- События для фронта писать в таблицу events (см. models.Event) через pipeline/events.py.
- Коммиты: осмысленные, с трейлером
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01R5A29CnhW3Qjp5mr5fatSU

## Границы агентов (чтобы не конфликтовать)
- Агент A владеет: backend/shortforge/api/**, app.py, auth, settings, миграции (alembic), seed.
- Агент B владеет: backend/shortforge/pipeline/**, providers/**, media/**, scripts/make_fixtures.py.
- Агент C владеет: frontend/**.
- models.py, api-spec.md — общие контракты: менять нельзя; если критично нужно — написать
  комментарий TODO-CONTRACT в коде и продолжить, интегратор решит.
