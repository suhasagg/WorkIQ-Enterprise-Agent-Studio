.PHONY: up down db install migrate api web test lint e2e screenshots reset

up:            ## full stack in Docker (http://localhost:3000)
	docker compose up --build
down:
	docker compose down
db:            ## only PostgreSQL + pgvector, for local development
	docker compose up -d postgres
install:
	uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
	cd web && npm ci
migrate:
	.venv/bin/alembic upgrade head
api:
	.venv/bin/uvicorn app.main:app --port 8140
web:
	cd web && npm run build && npm run start
test:
	.venv/bin/pytest -q
	cd web && npm test
lint:
	.venv/bin/ruff check app tests
	cd web && npm run lint && npm run typecheck
e2e:           ## needs `make api` and `make web` running
	cd web && npx playwright test
screenshots:   ## regenerates docs/screenshots from a freshly reset demo
	cd web && npm run screenshots
reset:
	./scripts/reset-demo.sh
