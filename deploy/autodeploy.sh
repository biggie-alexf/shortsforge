#!/usr/bin/env bash
# GitOps-обновление каждые 2 минуты. Два источника, по приоритету:
# 1) /data/shortforge/incoming.bundle — git-bundle, загруженный через /api/ops (когда
#    прямой push в GitHub из Claude невозможен): применяем, пушим в origin, деплоим.
# 2) origin/main в GitHub: если есть новые коммиты — pull и деплой.
set -uo pipefail
REPO=/opt/shortforge
LOG=/var/log/shortforge-deploy.log
DLOG=/data/shortforge/_deploy.log
BUNDLE=/data/shortforge/incoming.bundle
cd "$REPO" || exit 1

log() { echo "$(date -Is) $*" | tee -a "$LOG" >> "$DLOG"; }

redeploy() {
  cd "$REPO/deploy"
  if docker compose --env-file ../.env up -d --build >>"$LOG" 2>&1; then
    # Caddyfile примонтирован volume-ом — compose не видит его изменений
    docker compose --env-file ../.env restart caddy >>"$LOG" 2>&1
    # самообновление: иначе правки этого скрипта никогда не доехали бы
    install -m 0755 "$REPO/deploy/autodeploy.sh" /usr/local/bin/shortforge-autodeploy
    sleep 5
    docker compose --env-file ../.env exec -T api python -m shortforge.db_init >>"$LOG" 2>&1
    git -C "$REPO" log -1 --format='%h %cI %s' > /data/shortforge/_version
    log "deploy OK: $(git -C "$REPO" log -1 --format='%h %s')"
  else
    log "compose build FAILED"
  fi
  cd "$REPO"
}

if [ -f "$BUNDLE" ]; then
  log "bundle: применяю incoming.bundle"
  if git bundle verify "$BUNDLE" >>"$LOG" 2>&1 \
     && git fetch "$BUNDLE" 'refs/heads/main:refs/bundles/incoming' >>"$LOG" 2>&1; then
    git merge --ff-only refs/bundles/incoming >>"$LOG" 2>&1 \
      || git reset --hard refs/bundles/incoming >>"$LOG" 2>&1
    mv "$BUNDLE" "$BUNDLE.applied"
    git push origin main >>"$LOG" 2>&1 && log "bundle: запушен в origin" \
      || log "bundle: push в origin не прошёл (не критично)"
    redeploy
  else
    mv "$BUNDLE" "$BUNDLE.bad"
    log "bundle: битый, переименован в .bad"
  fi
  exit 0
fi

git fetch -q origin main 2>>"$LOG" || { log "fetch origin FAILED"; exit 1; }
LOCAL=$(git rev-parse HEAD); REMOTE=$(git rev-parse origin/main)
[ "$LOCAL" = "$REMOTE" ] && exit 0
log "deploy: $LOCAL -> $REMOTE"
git reset --hard origin/main >>"$LOG" 2>&1 || { log "reset FAILED"; exit 1; }
redeploy
