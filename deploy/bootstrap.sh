#!/usr/bin/env bash
# ShortForge: первичная установка на чистый Ubuntu-сервер (запускать от root).
# Использование: bash /opt/shortforge/deploy/bootstrap.sh
# Предполагается, что репо уже склонирован в /opt/shortforge (см. README).
set -euo pipefail
cd /opt/shortforge

echo "== 1/5 Docker и базовые пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq docker.io docker-compose-v2 git curl ca-certificates fail2ban
systemctl enable --now fail2ban  # боты долбят ssh на свежих Hetzner-IP
systemctl enable --now docker

echo "== 2/5 .env (создаётся один раз, секреты генерируются)"
if [ ! -f .env ]; then
  cat > .env <<ENV
APP_SECRET=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 43)
POSTGRES_PASSWORD=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 32)
DATA_DIR=/data/shortforge
ENV
  echo ".env создан"
fi
mkdir -p /data/shortforge

echo "== 3/5 Сборка и запуск контейнеров"
cd deploy
docker compose --env-file ../.env up -d --build
echo "жду Postgres..."
for i in $(seq 1 10); do
  docker compose --env-file ../.env exec -T api python -m shortforge.db_init && break
  echo "  БД ещё не готова, попытка $i"; sleep 5
done
docker compose --env-file ../.env exec -T api python -m shortforge.seed

echo "== 4/5 Автодеплой из GitHub (каждые 2 минуты)"
install -m 0755 autodeploy.sh /usr/local/bin/shortforge-autodeploy
cat > /etc/systemd/system/shortforge-autodeploy.service <<'UNIT'
[Unit]
Description=ShortForge autodeploy (git pull + compose up)
[Service]
Type=oneshot
ExecStart=/usr/local/bin/shortforge-autodeploy
UNIT
cat > /etc/systemd/system/shortforge-autodeploy.timer <<'UNIT'
[Unit]
Description=ShortForge autodeploy timer
[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now shortforge-autodeploy.timer

echo "== 5/5 Версия и статус"
git log -1 --format='%h %cI %s' > /data/shortforge/_version
docker compose --env-file ../.env ps
IP=$(curl -4s https://ifconfig.me || hostname -I | awk '{print $1}')
echo
echo "ГОТОВО: https://${IP}  (self-signed сертификат — принять в браузере)"
echo "Логин: admin / admin — смените пароль в Настройках."
