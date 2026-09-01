# ShortForge

Конвейер производства вертикальных Roblox-видео: батч «3 игры + идеи» → сценарий →
поиск летсплеев-доноров → нарезка → озвучка → сборка, с 4 ручными гейтами и
чатом-правок. Решения — в `docs/adr/`, термины — в `docs/glossary.md`,
контракт API — `docs/api-spec.md`.

## Логика
Статусы видео: `queued → scripting → GATE сценарий → hunting → cutting → GATE клипы →
voicing → rough_render → GATE черновик → master_render → GATE мастер → done`.
На каждом гейте доступен чат: агент превращает просьбу («сделай энергичнее», «замени
клип в блоке 2», «короче до 25 сек») в план инструментов, ты подтверждаешь — сервис
пересобирает только затронутые шаги и кладёт новую версию с changelog.

Без ключей все провайдеры работают в **mock-режиме** (синтетический геймплей,
espeak-озвучка) — флоу проверяется целиком. Ключи вводятся в «Настройках»
(шифруются в БД, ADR-009): Anthropic → реальные сценарии и агент, ElevenLabs →
голос канала, прокси/cookies → реальный поиск и скачивание с YouTube.

## Дев-запуск (без Docker)
```bash
# Postgres 16 и Redis должны быть запущены; БД shortforge (юзер shortforge/shortforge)
make dev-deps        # pip-зависимости
make db-init seed    # таблицы + admin/admin
make dev-api         # FastAPI :8000
make dev-worker      # arq-воркер
make dev-front       # Vite :5173 (проксирует /api и /media на :8000)
make test            # pytest (нужна БД shortforge_test)
```

## Деплой на сервер (Hetzner, Docker)
```bash
apt update && apt install -y docker.io docker-compose-v2 git
git clone <repo> /opt/shortforge && cd /opt/shortforge
cp .env.example .env   # заполнить APP_SECRET, POSTGRES_PASSWORD, DATA_DIR
cd deploy && docker compose --env-file ../.env up -d --build
docker compose exec api python -m shortforge.db_init
docker compose exec api python -m shortforge.seed   # печатает admin/admin — сменить пароль
```
Открывается на `https://IP` (self-signed, ADR-012). Домен: заменить `:443` на домен
в `deploy/Caddyfile` — TLS выпустится сам. Обновление: `deploy/deploy.sh`.

## После запуска
1. Зайти под admin/admin → Настройки → добавить пользователей и сменить пароль.
2. Ввести ключи: `anthropic_api_key`, `elevenlabs_api_key` (+ `elevenlabs_voice_id`),
   для YouTube — `ytdlp_proxy` (резидентный) и `ytdlp_cookies`.
3. Проверить панель «Провайдеры сейчас»: REAL/MOCK по каждому слою.
4. Доска → «Новый батч» → 3 строки (игра, идея, формат A/B) → вести по гейтам.

Готовые видео: гейт «Мастер» → «Скачать mp4» + заголовок/описание для заливки
(публикация на YouTube — вне скоупа MVP, ADR-008).
