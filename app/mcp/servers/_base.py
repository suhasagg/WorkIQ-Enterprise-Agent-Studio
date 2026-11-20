"""Shared helpers for the local *demo* MCP servers.

These servers speak real MCP (stdio or streamable HTTP) but are backed by deterministic synthetic data in
PostgreSQL (`demo_records`, `kpi_weekly`). They are simulations of enterprise systems, not live integrations.
The trusted backend injects `context` (workspace, actor, idempotency key, dry-run); it is never model-controlled.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from typing import Any

import psycopg
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
EXTERNAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

_conn: psycopg.Connection | None = None


def conn() -> psycopg.Connection:
    global _conn
    if _conn is None or _conn.closed:
        url = os.environ.get("DATABASE_URL", "postgresql://workiq:workiq@localhost:5432/workiq")
        _conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
    return _conn


def ws(context: dict | None) -> str:
    if not context or not context.get("workspace_id"):
        raise ValueError("missing trusted workspace context")
    return str(context["workspace_id"])


def records(context: dict | None, provider: str, kind: str) -> list[dict]:
    rows = conn().execute(
        "SELECT id, payload, created_at FROM demo_records WHERE workspace_id=%s AND provider=%s AND kind=%s ORDER BY created_at, id",
        (ws(context), provider, kind)).fetchall()
    return [{"id": r["id"], **r["payload"]} for r in rows]


def get_record(context: dict | None, provider: str, rec_id: str) -> dict | None:
    r = conn().execute("SELECT id, kind, payload FROM demo_records WHERE workspace_id=%s AND provider=%s AND id=%s",
                       (ws(context), provider, rec_id)).fetchone()
    return {"id": r["id"], "kind": r["kind"], **r["payload"]} if r else None


def create_record(context: dict | None, provider: str, kind: str, prefix: str, payload: dict) -> dict:
    """Idempotent create: the same idempotency key always returns the originally created record."""
    context = context or {}
    key = context.get("idempotency_key")
    payload = {**payload, "created_by": context.get("actor", "unknown"), "simulated": True}
    if context.get("dry_run"):
        return {"id": f"{prefix}-DRYRUN", **payload, "dry_run": True, "deduplicated": False}
    if key:
        existing = conn().execute("SELECT id, payload FROM demo_records WHERE idempotency_key=%s", (key,)).fetchone()
        if existing:
            return {"id": existing["id"], **existing["payload"], "deduplicated": True}
    rec_id = f"{prefix}-{uuid.uuid4().hex[:6].upper()}"
    row = conn().execute(
        """INSERT INTO demo_records(id, workspace_id, provider, kind, idempotency_key, payload) VALUES (%s,%s,%s,%s,%s,%s)
           ON CONFLICT (idempotency_key) DO NOTHING RETURNING id""",
        (rec_id, ws(context), provider, kind, key, Jsonb(payload))).fetchone()
    if row is None:  # lost a race on the same idempotency key
        existing = conn().execute("SELECT id, payload FROM demo_records WHERE idempotency_key=%s", (key,)).fetchone()
        return {"id": existing["id"], **existing["payload"], "deduplicated": True}
    return {"id": rec_id, **payload, "deduplicated": False}


def update_record(context: dict | None, provider: str, rec_id: str, patch: dict) -> dict:
    cur = get_record(context, provider, rec_id)
    if cur is None:
        raise ValueError(f"{rec_id} not found")
    if (context or {}).get("dry_run"):
        return {**cur, **patch, "dry_run": True}
    payload = {k: v for k, v in {**cur, **patch}.items() if k not in ("id", "kind")}
    conn().execute("UPDATE demo_records SET payload=%s WHERE id=%s AND workspace_id=%s", (Jsonb(payload), rec_id, ws(context)))
    return {"id": rec_id, **payload}


def as_json(x: Any) -> str:
    return json.dumps(x, default=str)


def run_server(mcp: FastMCP) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, default=0, help="serve streamable HTTP on this port instead of stdio")
    args = ap.parse_args()
    if args.http:
        mcp.settings.host = "127.0.0.1"
        mcp.settings.port = args.http
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
