# ADR-012: Репо и деплой
Дата: 2026-09-01 · Статус: принято
Код в приватном GitHub-репо. Деплой на Hetzner: git pull + docker compose up -d --build (скрипт deploy/deploy.sh). Запуск по IP без домена (Caddy self-signed / internal TLS); домен добавляется одной строкой в Caddyfile. Дев-режим без Docker: make dev поднимает postgres/redis локально и процессы напрямую.
