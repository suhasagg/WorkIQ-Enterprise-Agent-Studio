"""Work IQ Enterprise Agent Studio API."""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import db
from app.api import auth, evals, knowledge, legacy, mcp, observability, runs
from app.api.deps import rate_limit
from app.config import settings
from app.graph.runner import runner
from app.logging_setup import configure_logging
from app.mcp.manager import mcp_manager
from app.telemetry import configure_telemetry

log = structlog.get_logger()
state = {"ready": False, "seeding": False, "recovered": []}


async def _seed() -> None:
    from app.seed.demo import seed_all
    state["seeding"] = True
    try:
        await seed_all(history=True)
    except Exception:
        log.exception("demo_seed_failed")
    finally:
        state["seeding"] = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings.validate_for_production()
    configure_telemetry()
    await db.init_pool()
    await mcp_manager.start()
    await runner.graph()
    if settings.demo_mode:
        from app.seed.demo import seed_all
        await seed_all(history=False)  # identity, KPIs, provider records, documents (idempotent)
    state["recovered"] = await runner.recover()
    if state["recovered"]:
        log.info("runs_recovered", runs=state["recovered"])
    state["ready"] = True
    seed_task = asyncio.create_task(_seed()) if settings.demo_mode and settings.seed_history else None
    try:
        yield
    finally:
        state["ready"] = False
        if seed_task:
            seed_task.cancel()
        await runner.shutdown()
        await mcp_manager.stop()
        await db.close_pools()


app = FastAPI(title="Work IQ Enterprise Agent Studio", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID"])


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    structlog.contextvars.bind_contextvars(request_id=rid)
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled_error", path=request.url.path)
        response = JSONResponse({"detail": "internal error", "request_id": rid}, status_code=500)
    finally:
        structlog.contextvars.clear_contextvars()
    response.headers["X-Request-ID"] = rid
    response.headers["X-Content-Type-Options"] = "nosniff"
    if not request.url.path.startswith(("/health", "/metrics")):
        log.info("request", method=request.method, path=request.url.path, status=response.status_code,
                 ms=int((time.perf_counter() - t0) * 1000), request_id=rid)
    return response


@app.get("/health/live", tags=["health"])
async def live():
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def ready():
    checks = {"app": state["ready"]}
    try:
        checks["database"] = await db.fetch_val("SELECT 1") == 1
    except Exception:
        checks["database"] = False
    servers = {c.id: c.status for c in mcp_manager.connections.values()}
    checks["mcp"] = any(s == "connected" for s in servers.values())
    ok = all(checks.values())
    return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks, "mcp_servers": servers,
                         "seeding_demo_activity": state["seeding"], "recovered_runs": state["recovered"]},
                        status_code=200 if ok else 503)


@app.get("/metrics", tags=["health"])
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


for r in (auth, runs, mcp, knowledge, observability, evals, legacy):
    app.include_router(r.router, dependencies=[Depends(rate_limit)])
