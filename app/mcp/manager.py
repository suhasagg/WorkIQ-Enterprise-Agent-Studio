"""MCP client manager: persistent sessions to registered MCP servers over stdio or streamable HTTP.

Uses the official `mcp` SDK. Each server gets one long-lived ClientSession owned by a background task
(the SDK's transports must be entered and exited from the same task). Credentials are resolved from the
environment at connect time, sent only to their own server and never persisted or returned by the API.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from app import db
from app.config import settings
from app.logging_setup import log

DEMO_MODULES = {
    "workiq": ("app.mcp.servers.workiq_m365", "Work IQ (Microsoft 365)", "microsoft365",
               "Path-oriented Microsoft 365 access through the 10-tool Work IQ MCP vocabulary.",
               ["Mail.Read", "Mail.Send", "Calendars.ReadWrite", "OnlineMeetingTranscript.Read", "Chat.ReadWrite"]),
    "tracker": ("app.mcp.servers.tracker", "Work Tracker", "jira-style",
                "Issue and task tracking: search, create and update follow-up work.", ["issues:read", "issues:write"]),
    "helpdesk": ("app.mcp.servers.helpdesk", "Support Desk", "helpdesk",
                 "Customer support tickets, internal notes and escalations.", ["tickets:read", "tickets:write", "tickets:escalate"]),
    "metrics": ("app.mcp.servers.metrics", "Business Metrics", "kpi-warehouse",
                "Weekly KPI warehouse for analytics and executive reporting.", ["metrics:read"]),
}


@dataclass
class ToolInfo:
    name: str
    description: str
    input_schema: dict
    read_only: bool
    destructive: bool
    external: bool

    def meta(self) -> dict:
        return {"read_only": self.read_only, "destructive": self.destructive, "external": self.external}

    def public(self) -> dict:
        schema = json.loads(json.dumps(self.input_schema))
        schema.get("properties", {}).pop("context", None)  # trusted context is injected server-side
        return {"name": self.name, "description": self.description, "input_schema": schema, **self.meta()}


@dataclass
class ServerConnection:
    cfg: dict
    status: str = "disconnected"  # disconnected | connecting | connected | error | not_configured | disabled
    last_error: str | None = None
    tools: dict[str, ToolInfo] = field(default_factory=dict)
    server_info: dict = field(default_factory=dict)
    connected_at: str | None = None
    last_ping_ms: int | None = None
    last_checked: str | None = None
    session: ClientSession | None = None
    _task: asyncio.Task | None = None
    _ready: asyncio.Event = field(default_factory=asyncio.Event)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def id(self) -> str:
        return self.cfg["id"]

    def credential_configured(self) -> bool | None:
        env = self.cfg.get("credential_env")
        return None if not env else bool(os.environ.get(env) or getattr(settings, env.lower(), ""))

    def _credential(self) -> str:
        env = self.cfg.get("credential_env") or ""
        return os.environ.get(env) or getattr(settings, env.lower(), "") if env else ""

    @asynccontextmanager
    async def _transport(self):
        if self.cfg["transport"] == "stdio":
            cmd = self.cfg["command"]
            # Minimal child environment: demo servers get the database URL but no application secrets.
            env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "PYTHONPATH", "VIRTUAL_ENV")}
            env["DATABASE_URL"] = settings.database_url
            params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=env, cwd=os.getcwd())
            async with stdio_client(params, errlog=sys.stderr) as (r, w):
                yield r, w
        else:
            headers = {}
            if self._credential():
                headers["Authorization"] = f"Bearer {self._credential()}"
            elif self.id == "workiq" and settings.workiq_token_scope:
                from azure.identity.aio import DefaultAzureCredential
                cred = DefaultAzureCredential()
                try:
                    headers["Authorization"] = "Bearer " + (await cred.get_token(settings.workiq_token_scope)).token
                finally:
                    await cred.close()
            async with streamablehttp_client(self.cfg["url"], headers=headers or None, timeout=settings.tool_timeout_seconds) as (r, w, _):
                yield r, w

    async def _run(self) -> None:
        try:
            async with self._transport() as (r, w), ClientSession(r, w) as session:
                init = await session.initialize()
                self.server_info = {"name": init.serverInfo.name, "version": init.serverInfo.version,
                                    "protocol": init.protocolVersion}
                listed = await session.list_tools()
                self.tools = {}
                for t in listed.tools:
                    a = t.annotations
                    self.tools[t.name] = ToolInfo(
                        t.name, t.description or "", t.inputSchema or {},
                        read_only=bool(a and a.readOnlyHint), destructive=bool(a and a.destructiveHint),
                        external=bool(a and a.openWorldHint and not a.readOnlyHint))
                self.session, self.status, self.last_error = session, "connected", None
                self.connected_at = datetime.now(UTC).isoformat()
                self._ready.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # ExceptionGroup from anyio task groups included
            self.status, self.last_error = "error", _describe(e)
            log.warning("mcp_connection_failed", server=self.id, error=self.last_error)
        finally:
            self.session = None
            if self.status == "connected":
                self.status = "disconnected"
            self._ready.set()

    async def start(self, timeout: float = 20) -> None:
        if not self.cfg.get("enabled", True):
            self.status = "disabled"
            return
        if self.cfg.get("credential_env") and not self.credential_configured():
            self.status, self.last_error = "not_configured", f"credential {self.cfg['credential_env']} is not set"
            return
        await self.stop()
        self.status, self._ready, self._stop = "connecting", asyncio.Event(), asyncio.Event()
        self._task = asyncio.create_task(self._run(), name=f"mcp:{self.id}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except TimeoutError:
            self.status, self.last_error = "error", "connection timed out"
            await self.stop()

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._stop.set()
            try:
                await asyncio.wait_for(self._task, 5)
            except (TimeoutError, asyncio.CancelledError, Exception):
                self._task.cancel()
        self._task = None

    async def ping(self) -> bool:
        self.last_checked = datetime.now(UTC).isoformat()
        if not self.session:
            return False
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(self.session.send_ping(), 5)
            self.last_ping_ms = int((time.perf_counter() - t0) * 1000)
            return True
        except Exception as e:
            self.status, self.last_error = "error", _describe(e)
            return False

    async def call(self, tool: str, args: dict, timeout: float) -> Any:
        if not self.session:
            raise ConnectionError(f"MCP server '{self.id}' is not connected ({self.status})")
        res = await asyncio.wait_for(
            self.session.call_tool(tool, args, read_timeout_seconds=timedelta(seconds=timeout)), timeout + 1)
        text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
        if res.isError:
            raise ToolError(text or "tool returned an error")
        if res.structuredContent is not None:
            sc = res.structuredContent
            return sc.get("result", sc) if set(sc.keys()) == {"result"} else sc
        try:
            return json.loads(text)
        except Exception:
            return {"text": text}

    def public(self) -> dict:
        c = self.cfg
        return {"id": c["id"], "name": c["name"], "description": c.get("description", ""), "provider": c["provider"],
                "kind": c["kind"], "transport": c["transport"], "enabled": c.get("enabled", True),
                "builtin": c.get("builtin", False), "scopes": c.get("scopes", []), "status": self.status,
                "last_error": self.last_error, "server_info": self.server_info, "connected_at": self.connected_at,
                "last_ping_ms": self.last_ping_ms, "last_checked": self.last_checked,
                "endpoint": c.get("url") if c["transport"] == "streamable_http" else "local subprocess (stdio)",
                "credential": {"required": bool(c.get("credential_env")), "env_var": c.get("credential_env"),
                               "configured": self.credential_configured()},
                "tool_count": len(self.tools)}


class ToolError(Exception):
    pass


def _describe(e: BaseException) -> str:
    if isinstance(e, BaseExceptionGroup) and e.exceptions:
        return _describe(e.exceptions[0])
    return f"{type(e).__name__}: {e}"[:300]


def builtin_servers() -> list[dict]:
    out = []
    for sid, (module, name, provider, desc, scopes) in DEMO_MODULES.items():
        if sid == "workiq" and settings.workiq_mcp_endpoint:
            out.append({"id": sid, "name": name, "provider": provider, "description": desc, "kind": "live",
                        "transport": "streamable_http", "url": settings.workiq_mcp_endpoint, "command": None,
                        "credential_env": None, "scopes": scopes})
        else:
            out.append({"id": sid, "name": name + " (demo)", "provider": provider,
                        "description": desc + " Simulated provider with synthetic data.", "kind": "demo",
                        "transport": "stdio", "command": [sys.executable, "-m", module], "url": None,
                        "credential_env": None, "scopes": scopes})
    out.append({"id": "github", "name": "GitHub", "provider": "github", "kind": "live", "transport": "streamable_http",
                "description": "GitHub's remote MCP server. Requires GITHUB_TOKEN; not verified in the demo environment.",
                "url": settings.github_mcp_url, "command": None, "credential_env": "GITHUB_TOKEN",
                "scopes": ["repo", "read:org"]})
    return out


class MCPManager:
    def __init__(self) -> None:
        self.connections: dict[str, ServerConnection] = {}
        self._health_task: asyncio.Task | None = None

    async def sync_registry(self) -> None:
        for s in builtin_servers():
            await db.execute(
                """INSERT INTO mcp_servers(id,name,description,provider,kind,transport,command,url,credential_env,scopes,builtin)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)
                   ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, description=EXCLUDED.description, kind=EXCLUDED.kind,
                     transport=EXCLUDED.transport, command=EXCLUDED.command, url=EXCLUDED.url,
                     credential_env=EXCLUDED.credential_env, scopes=EXCLUDED.scopes""",
                (s["id"], s["name"], s["description"], s["provider"], s["kind"], s["transport"],
                 db.J(s["command"]), s["url"], s["credential_env"], db.J(s["scopes"])))

    async def start(self) -> None:
        await self.sync_registry()
        rows = await db.fetch_all("SELECT * FROM mcp_servers ORDER BY builtin DESC, id")
        self.connections = {r["id"]: ServerConnection(r) for r in rows}
        await asyncio.gather(*(c.start() for c in self.connections.values()))
        self._health_task = asyncio.create_task(self._health_loop(), name="mcp-health")

    async def stop(self) -> None:
        if self._health_task:
            self._health_task.cancel()
        await asyncio.gather(*(c.stop() for c in self.connections.values()), return_exceptions=True)
        self.connections = {}

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            for c in list(self.connections.values()):
                if c.status == "connected":
                    await c.ping()
                elif c.status == "error":
                    await c.start()  # bounded self-healing: one reconnect attempt per interval

    async def register(self, cfg: dict) -> ServerConnection:
        await db.execute(
            """INSERT INTO mcp_servers(id,name,description,provider,kind,transport,command,url,credential_env,scopes,created_by)
               VALUES (%s,%s,%s,%s,'live','streamable_http',NULL,%s,%s,%s,%s)""",
            (cfg["id"], cfg["name"], cfg.get("description", ""), cfg.get("provider", "custom"), cfg["url"],
             cfg.get("credential_env"), db.J(cfg.get("scopes", [])), cfg.get("created_by")))
        row = await db.fetch_one("SELECT * FROM mcp_servers WHERE id=%s", (cfg["id"],))
        conn = ServerConnection(row)
        self.connections[row["id"]] = conn
        await conn.start()
        return conn

    async def unregister(self, server_id: str) -> None:
        conn = self.connections.pop(server_id, None)
        if conn:
            await conn.stop()
        await db.execute("DELETE FROM mcp_servers WHERE id=%s AND builtin=false", (server_id,))

    def get(self, server_id: str) -> ServerConnection | None:
        return self.connections.get(server_id)

    async def reconnect(self, server_id: str) -> ServerConnection:
        conn = self.connections[server_id]
        await conn.start()
        await conn.ping()
        return conn


mcp_manager = MCPManager()
