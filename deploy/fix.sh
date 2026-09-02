#!/usr/bin/env bash
# «Ремонтная кнопка»: привести сервер в соответствие с репозиторием.
# Запуск: bash /opt/shortforge/deploy/fix.sh
set -x
cd /opt/shortforge/deploy || exit 1
install -m 0755 autodeploy.sh /usr/local/bin/shortforge-autodeploy
docker compose --env-file ../.env up -d --build
docker compose --env-file ../.env restart caddy
sleep 4
set +x
echo "=== Проверка входа ==="
curl -sI http://127.0.0.1/ | head -2
curl -skI https://178.104.185.191/ | head -2
echo "=== Статус контейнеров ==="
docker ps --format '{{.Names}} {{.Status}}'
