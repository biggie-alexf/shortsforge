#!/usr/bin/env bash
# на сервере: ./deploy.sh — тянет main и перекатывает контейнеры
set -euo pipefail
cd "$(dirname "$0")"
git -C .. pull --ff-only
docker compose --env-file ../.env up -d --build
docker compose --env-file ../.env ps
