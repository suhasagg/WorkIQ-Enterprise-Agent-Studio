from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app import db
from app.api.deps import ctx_for, not_found
from app.audit import audit
from app.mcp.gateway import gateway
from app.mcp.manager import mcp_manager
from app.policy import policy
from app.security import ROLE_SCOPES, Principal, principal, require

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


def _conn(server_id: str):
    conn = mcp_manager.get(server_id)
    if not conn:
        raise not_found("MCP server")
    return conn


async def _tools(conn, p: Principal) -> list[dict]:
    disabled = {r["tool"] for r in await db.fetch_all(
        "SELECT tool FROM mcp_tool_settings WHERE workspace_id=%s AND server_id=%s AND enabled=false", (p.tenant, conn.id))}
    out = []
    for t in conn.tools.values():
        is_write, risk, why = policy.classify(conn.id, t.name, {}, t.meta())
        out.append({**t.public(), "enabled": t.name not in disabled, "is_write": is_write, "risk": risk,
                    "policy": "requires human approval" if is_write else "allowed for readers", "policy_reason": why,
                    "required_scope": "workiq:write" if is_write else "workiq:read",
                    "roles": [r for r, s in ROLE_SCOPES.items() if ("workiq:write" if is_write else "workiq:read") in s]})
    return sorted(out, key=lambda t: (t["is_write"], t["name"]))


async def _stats(p: Principal) -> dict:
    rows = await db.fetch_all(
        """SELECT server, count(*) AS calls, count(*) FILTER (WHERE status='ok') AS ok,
                  count(*) FILTER (WHERE status='failed') AS failed,
                  count(*) FILTER (WHERE status IN ('denied','approval_required')) AS governed,
                  COALESCE(round(avg(latency_ms) FILTER (WHERE status='ok')),0)::int AS avg_latency_ms, max(created_at) AS last_call
           FROM tool_executions WHERE workspace_id=%s GROUP BY server""", (p.tenant,))
    return {r["server"]: r for r in rows}


@router.get("/servers")
async def servers(p: Principal = Depends(principal)):
    stats = await _stats(p)
    return [{**c.public(), "stats": stats.get(c.id)} for c in mcp_manager.connections.values()]


@router.get("/servers/{server_id}")
async def server(server_id: str, p: Principal = Depends(principal)):
    conn = _conn(server_id)
    return {**conn.public(), "tools": await _tools(conn, p), "stats": (await _stats(p)).get(server_id),
            "recent": await db.fetch_all(
                """SELECT id, tool, status, decision, error, latency_ms, attempt, run_id, is_write, created_at FROM tool_executions
                   WHERE workspace_id=%s AND server=%s ORDER BY id DESC LIMIT 25""", (p.tenant, server_id))}


class Register(BaseModel):
    id: str = Field(..., pattern="^[a-z][a-z0-9_-]{1,30}$")
    name: str = Field(..., min_length=2, max_length=80)
    url: str = Field(..., pattern=r"^https?://\S+$", max_length=400)
    description: str = Field("", max_length=400)
    provider: str = Field("custom", max_length=40)
    credential_env: str | None = Field(None, max_length=60)
    scopes: list[str] = []


@router.post("/servers", status_code=201)
async def register(body: Register, p: Principal = Depends(require("connector:admin"))):
    """Register a remote MCP server (streamable HTTP). Credentials are referenced by environment variable name only;
    secret values are never accepted by, stored in or returned from this API."""
    if mcp_manager.get(body.id):
        raise HTTPException(409, "a server with this id already exists")
    if body.credential_env and not re.fullmatch(r"[A-Z][A-Z0-9_]{2,59}", body.credential_env):
        raise HTTPException(400, "credential_env must be an environment variable name, e.g. ACME_MCP_TOKEN")
    conn = await mcp_manager.register({**body.model_dump(), "created_by": p.subject})
    await audit.append({"workspace_id": p.tenant, "actor": p.subject, "action": "mcp.register", "resource": body.id,
                        "decision": "allow", "detail": {"url": body.url, "status": conn.status}})
    return conn.public()


@router.delete("/servers/{server_id}", status_code=204)
async def unregister(server_id: str, p: Principal = Depends(require("connector:admin"))):
    conn = _conn(server_id)
    if conn.cfg.get("builtin"):
        raise HTTPException(400, "built-in servers cannot be removed")
    await mcp_manager.unregister(server_id)
    await audit.append({"workspace_id": p.tenant, "actor": p.subject, "action": "mcp.unregister", "resource": server_id, "decision": "allow"})


@router.post("/servers/{server_id}/reconnect")
async def reconnect(server_id: str, p: Principal = Depends(require("connector:admin"))):
    _conn(server_id)
    conn = await mcp_manager.reconnect(server_id)
    await audit.append({"workspace_id": p.tenant, "actor": p.subject, "action": "mcp.reconnect", "resource": server_id,
                        "decision": conn.status})
    return conn.public()


@router.post("/servers/{server_id}/health")
async def health(server_id: str, p: Principal = Depends(principal)):
    conn = _conn(server_id)
    ok = await conn.ping()
    return {"ok": ok, **conn.public()}


class ToolSetting(BaseModel):
    enabled: bool


@router.put("/servers/{server_id}/tools/{tool}")
async def set_tool(server_id: str, tool: str, body: ToolSetting, p: Principal = Depends(require("connector:admin"))):
    conn = _conn(server_id)
    if tool not in conn.tools:
        raise not_found("tool")
    await db.execute(
        """INSERT INTO mcp_tool_settings(workspace_id, server_id, tool, enabled, updated_by) VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (workspace_id, server_id, tool) DO UPDATE SET enabled=EXCLUDED.enabled, updated_by=EXCLUDED.updated_by, updated_at=now()""",
        (p.tenant, server_id, tool, body.enabled, p.subject))
    await audit.append({"workspace_id": p.tenant, "actor": p.subject, "action": "mcp.tool_setting", "resource": f"{server_id}.{tool}",
                        "decision": "enabled" if body.enabled else "disabled"})
    return {"server": server_id, "tool": tool, "enabled": body.enabled}


class Invoke(BaseModel):
    args: dict = {}


@router.post("/servers/{server_id}/tools/{tool}/invoke")
async def invoke(server_id: str, tool: str, body: Invoke, p: Principal = Depends(principal)):
    """Tool console. Goes through the same governed gateway as agents: schema validation, RBAC, policy and audit.
    State-changing tools return `approval_required` here; they can only run inside a workflow with an approved action."""
    conn = _conn(server_id)
    if tool not in conn.tools:
        raise not_found("tool")
    return await gateway.call(ctx_for(p, node="tool-console", agent="user"), server_id, tool, body.args)


@router.get("/executions")
async def executions(p: Principal = Depends(principal), server: str | None = None, limit: int = Query(100, ge=1, le=500)):
    where, params = ["workspace_id=%s"], [p.tenant]
    if server:
        where.append("server=%s")
        params.append(server)
    return await db.fetch_all(
        f"""SELECT id, run_id, server, tool, args, status, decision, error, latency_ms, attempt, node, agent, is_write, origin, created_at
            FROM tool_executions WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %s""", (*params, limit))
