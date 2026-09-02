#!/usr/bin/env bash
# GitOps: если в origin/main есть новые коммиты — pull, пересборка, миграция схемы.
# Лог: /var/log/shortforge-deploy.log + /data/shortforge/_deploy.log (виден в /api/ops/status).
set -uo pipefail
REPO=/opt/shortforge
LOG=/var/log/shortforge-deploy.log
DLOG=/data/shortforge/_deploy.log
cd "$REPO" || exit 1

log() { echo "$(date -Is) $*" | tee -a "$LOG" >> "$DLOG"; }

git fetch -q origin main 2>>"$LOG" || { log "fetch FAILED"; exit 1; }
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
[ "$LOCAL" = "$REMOTE" ] && exit 0

log "deploy: $LOCAL -> $REMOTE"
git reset --hard origin/main >>"$LOG" 2>&1 || { log "reset FAILED"; exit 1; }
cd deploy
if docker compose --env-file ../.env up -d --build >>"$LOG" 2>&1; then
  sleep 5
  docker compose --env-file ../.env exec -T api python -m shortforge.db_init >>"$LOG" 2>&1
  git -C "$REPO" log -1 --format='%h %cI %s' > /data/shortforge/_version
  log "deploy OK: $(git -C "$REPO" log -1 --format='%h %s')"
else
  log "compose build FAILED — остаёмся на $LOCAL"
fi
