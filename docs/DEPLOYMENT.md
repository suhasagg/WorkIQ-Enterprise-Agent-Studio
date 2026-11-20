# Deployment

## Local demo with Docker Compose

```bash
docker compose up --build
```

| Service | Image | Port | Health check |
|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | internal | `pg_isready` |
| `api` | built from `./Dockerfile` | 8140 | `GET /health/ready` |
| `web` | built from `./web/Dockerfile` (Next.js standalone) | 3000 | `GET /` |

Start order is enforced with health conditions. The API container runs `alembic upgrade head` and then Uvicorn
(`scripts/start.sh`). In demo mode it seeds two synthetic workspaces and, on first start, runs a few workflows and
baseline evaluations (about ten seconds). `GET /health/ready` reports `seeding_demo_activity`.

This stack was built and started with Docker Compose and the Playwright suite passed against it (see the test report).

Reset the demo data: `docker compose down -v`, or `make reset` for a local database.

## Local development

```bash
cp .env.example .env
make db install migrate
make api        # http://localhost:8140, OpenAPI at /docs
make web        # http://localhost:3000
```

`cd web && npm run dev` gives hot reload. The web app proxies `/api/*` to `API_URL` (default `http://localhost:8140`).

## Configuration

All settings are environment variables; see [`.env.example`](../.env.example). The important ones:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local PostgreSQL | must have the `vector` extension available |
| `ENV` | `dev` | `prod` enforces a strong `JWT_SECRET` and refuses default demo credentials |
| `JWT_SECRET` | dev value | signing key for session tokens |
| `DEMO_MODE`, `SEED_HISTORY` | `true` | synthetic data, simulated MCP servers, demo logins |
| `MODEL_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_MODEL` | `auto`, empty | `auto` uses OpenAI only when a key is set, otherwise the deterministic model |
| `EMBEDDING_PROVIDER` | `auto` | `hash` (local) or `openai` |
| `GITHUB_TOKEN` | empty | connects the optional GitHub remote MCP server |
| `ALLOW_FAULT_INJECTION` | `true` | demo-only failure simulation |
| `RUN_TIMEOUT_SECONDS`, `TOOL_TIMEOUT_SECONDS`, `MAX_TOOL_CALLS_PER_RUN` | 300, 20, 40 | execution limits |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | export traces to a collector |
| `API_URL` (web, build time) | `http://localhost:8140` | backend address used by the Next.js proxy |

Changing the embedding model changes the vector dimension; re-index documents after switching.

## Endpoints for operations

| Endpoint | Use |
|---|---|
| `GET /health/live` | liveness |
| `GET /health/ready` | readiness: database, MCP servers, seeding state, recovered runs |
| `GET /metrics` | Prometheus metrics (runs, node latency, tool calls, model calls) |
| `GET /docs` | OpenAPI |

## Running in production

The repository ships a working single-node deployment, not a production platform. To run it for real:

1. **Database**: managed PostgreSQL 16 with pgvector, backups and TLS. Run `alembic upgrade head` as a release step.
2. **Secrets**: `JWT_SECRET`, provider keys and MCP tokens from a secret manager, injected as environment variables.
3. **Settings**: `ENV=prod`, `DEMO_MODE=false`, `SEED_HISTORY=false`, `ALLOW_FAULT_INJECTION=false`, `COOKIE_SECURE=true`, real `CORS_ORIGINS`.
4. **Identity**: replace the demo login with your identity provider.
5. **Ingress**: HTTPS, with response buffering disabled for `/api/runs/*/stream` (the API already sends `X-Accel-Buffering: no`).
6. **Replicas**: run **one** API replica. Workflow runs execute inside the API process and the rate limiter and event fan-out are in-process. A crash or restart is safe (runs are recovered from checkpoints), but two replicas would both try to recover the same runs. Scaling out needs a job queue with a per-run lease, a shared rate limiter and `LISTEN/NOTIFY` or a broker for events.
7. **Uploads**: mount persistent storage at `UPLOAD_DIR`, or move file storage to an object store.
8. **Observability**: scrape `/metrics`, set `OTEL_EXPORTER_OTLP_ENDPOINT`, ship the JSON logs.

## Operations

| Situation | What to do |
|---|---|
| Run stuck in `waiting_approval` | Decide it in the Approval Center, or cancel the run. |
| API restarted mid-run | Nothing: `runner.recover()` continues `running` and `queued` runs from their checkpoint. `GET /health/ready` lists them. |
| Run failed | Open the run, read the timeline, use **Retry** (continues from the last checkpoint). |
| MCP server shows `error` | MCP Integrations → Health check or Reconnect. Check the credential variable for remote servers. |
| Document stuck or failed | Knowledge → Re-index; the error is shown on the row. |
| Audit integrity | Observability → Verify chain, or `GET /api/audit/verify`. |

## CI

`.github/workflows/ci.yml` has three jobs: `backend` (ruff, pytest with a pgvector service), `frontend` (eslint,
tsc, Vitest, build) and `e2e` (starts the API and the web app, runs Playwright).
