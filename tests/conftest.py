"""Integration test harness: a real PostgreSQL + pgvector database, real MCP stdio servers and the real compiled graph.

Requires a reachable Postgres (TEST_DATABASE_ADMIN_URL, default: the local docker container). A dedicated
`workiq_test` database is dropped and re-created for every test session.
"""
import os

ADMIN = os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://workiq:workiq@localhost:5432/postgres")
TEST_DB = ADMIN.rsplit("/", 1)[0] + "/workiq_test"
os.environ.update({"DATABASE_URL": TEST_DB, "ENV": "test", "DEMO_MODE": "true", "SEED_HISTORY": "false",
                   "MODEL_PROVIDER": "deterministic", "EMBEDDING_PROVIDER": "hash", "RATE_LIMIT_PER_MINUTE": "100000",
                   "UPLOAD_DIR": "/tmp/workiq-test-uploads", "GITHUB_TOKEN": "", "OPENAI_API_KEY": ""})

import httpx  # noqa: E402
import psycopg  # noqa: E402
import pytest_asyncio  # noqa: E402
from alembic.config import Config  # noqa: E402

from alembic import command  # noqa: E402
from app.security import ROLE_SCOPES, Principal  # noqa: E402


def _reset_db() -> None:
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS workiq_test WITH (FORCE)")
        c.execute("CREATE DATABASE workiq_test")
    command.upgrade(Config("alembic.ini"), "head")


@pytest_asyncio.fixture(scope="session")
async def app():
    _reset_db()
    from app.main import app as fastapi_app
    async with fastapi_app.router.lifespan_context(fastapi_app):
        yield fastapi_app


@pytest_asyncio.fixture(scope="session")
async def tokens(app):
    out = {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        for role, email in {"admin": "admin@meridian.example", "approver": "approver@meridian.example",
                            "operator": "operator@meridian.example", "viewer": "viewer@meridian.example",
                            "globex": "admin@globex.example"}.items():
            r = await c.post("/api/auth/login", json={"email": email, "password": "workiq-demo"})
            assert r.status_code == 200, r.text
            out[role] = r.json()["token"]
    return out


class Api:
    def __init__(self, app, tokens):
        self.app, self.tokens = app, tokens

    async def call(self, role, method, path, **kw):
        headers = {"authorization": f"Bearer {self.tokens[role]}"} if role else {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test", headers=headers) as c:
            return await c.request(method, path, **kw)

    async def get(self, role, path, **kw):
        return await self.call(role, "GET", path, **kw)

    async def post(self, role, path, json=None, **kw):
        return await self.call(role, "POST", path, json=json, **kw)

    async def wait_run(self, run_id, role="operator", until=("completed", "failed", "cancelled", "waiting_approval", "paused"), timeout=30):
        import asyncio
        for _ in range(int(timeout / 0.05)):
            run = (await self.get(role, f"/api/runs/{run_id}")).json()
            if run["status"] in until:
                return run
            await asyncio.sleep(0.05)
        raise AssertionError(f"run {run_id} still {run['status']} after {timeout}s")

    async def run_workflow(self, workflow=None, request=None, options=None, role="operator"):
        r = await self.post(role, "/api/runs", {"workflow": workflow, "request": request, "options": options or {}})
        assert r.status_code == 201, r.text
        return await self.wait_run(r.json()["id"])

    async def decide_all(self, run_id, decision="approve", comment="ok", role="approver"):
        pending = [a for a in (await self.get(role, f"/api/runs/{run_id}")).json()["approvals"] if a["status"] == "pending"]
        for a in pending:
            r = await self.post(role, f"/api/approvals/{a['id']}/decision", {"decision": decision, "comment": comment})
            assert r.status_code == 200, r.text
        return pending


@pytest_asyncio.fixture(scope="session")
async def api(app, tokens):
    return Api(app, tokens)


def make_principal(role: str, tenant: str = "meridian") -> Principal:
    return Principal(f"usr_{role}", tenant, ROLE_SCOPES[role], role, role.title(), f"{role}@{tenant}.example")
