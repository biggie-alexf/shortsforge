SHELL := /bin/bash
export DATABASE_URL ?= postgresql+asyncpg://shortforge:shortforge@127.0.0.1:5432/shortforge
export REDIS_URL ?= redis://127.0.0.1:6379/0
export APP_SECRET ?= dev-secret
export DATA_DIR ?= /home/user/shortforge-data

dev-deps:
	pip install --break-system-packages -r backend/requirements.txt

db-init:
	cd backend && python -m shortforge.db_init

seed:
	cd backend && python -m shortforge.seed

dev-api:
	cd backend && uvicorn shortforge.api.app:app --host 0.0.0.0 --port 8000 --reload

dev-worker:
	cd backend && arq shortforge.pipeline.worker.WorkerSettings

dev-front:
	cd frontend && npm run dev -- --host

fixtures:
	cd backend && python -m scripts.make_fixtures

test:
	cd backend && python -m pytest tests -x -q

e2e:
	cd backend && python -m pytest tests/test_e2e_mock.py -x -q -s
