# ADR-007: Технологический стек
Дата: 2026-09-01 · Статус: принято

## Решение
- Backend: Python 3.12, FastAPI, SQLAlchemy, Postgres 16, Redis (очередь arq/RQ), воркеры в отдельных контейнерах.
- Медиа: ffmpeg, yt-dlp, PySceneDetect, faster-whisper (large-v3 int8 CPU), WhisperX-выравнивание, ASS-сабы.
- AI: Anthropic API (сценарист + чат-агент), ElevenLabs API (TTS, 1 закреплённый голос + 2 для формата C позже).
- Frontend: React + Vite (SPA), SSE для прогресса, видео-превью — HTML5 video с mp4-сниппетами.
- Деплой: Docker Compose на одном сервере, Caddy (TLS + reverse proxy), бэкап Postgres + мастеров в объектное хранилище.

## Обоснование
Весь медиастек родной для Python; команда поддерживает вайбкодед-фронты; один хост укладывается в бюджет.
