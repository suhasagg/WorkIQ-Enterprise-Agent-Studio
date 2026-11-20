"""Governed MCP tool execution: schema validation, policy, approval verification, budgets, timeouts,
bounded retries for read-only tools, idempotency context, tracing and audit. Every tool call goes through here."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from typing import Any

import jsonschema

from app import db
from app.audit import audit
from app.config import settings
from app.context import RunCtx
from app.mcp.manager import ToolError, mcp_manager
from app.metrics import TOOL_CALLS, TOOL_LATENCY
from app.policy import policy
from app.redaction import redact
from app.telemetry import span

_fault_counts: dict[tuple[str, str], int] = defaultdict(int)


def params_hash(args: dict) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()


class ToolGateway:
    async def tool_enabled(self, workspace_id: str, server: str, tool: str) -> bool:
        row = await db.fetch_one("SELECT enabled FROM mcp_tool_settings WHERE workspace_id=%s AND server_id=%s AND tool=%s",
                                 (workspace_id, server, tool))
        return True if row is None else row["enabled"]

    async def evaluate(self, ctx: RunCtx, server: str, tool: str, args: dict, approved: bool = False):
        """Policy decision without executing (used by the Compliance agent to classify proposed actions)."""
        conn = mcp_manager.get(server)
        info = conn.tools.get(tool) if conn else None
        enabled = await self.tool_enabled(ctx.workspace_id, server, tool)
        return policy.decide(ctx.principal, tool, args, approved, server=server, meta=info.meta() if info else None,
                             tool_enabled=enabled)

    async def _verify_approval(self, ctx: RunCtx, approval_id: str | None, server: str, tool: str, args: dict) -> str | None:
        """Server-side check that this exact action (tool + parameters) was approved for this run."""
        if not approval_id:
            return "no approval reference supplied"
        a = await db.fetch_one("SELECT * FROM approvals WHERE id=%s AND workspace_id=%s", (approval_id, ctx.workspace_id))
        if not a or a["run_id"] != ctx.run_id:
            return "approval not found for this run"
        if a["status"] not in ("approved", "edited"):
            return f"approval status is '{a['status']}'"
        if a["server"] != server or a["tool"] != tool:
            return "approval does not match the requested tool"
        approved_params = a["edited_params"] if a["status"] == "edited" else a["params"]
        if params_hash(approved_params) != params_hash(args):
            return "parameters differ from what was approved"
        return None

    async def call(self, ctx: RunCtx, server: str, tool: str, args: dict, *, approval_id: str | None = None,
                   idempotency_key: str | None = None) -> dict:
        t0 = time.perf_counter()
        args = {k: v for k, v in args.items() if k != "context"}  # callers can never supply trusted context
        conn = mcp_manager.get(server)
        info = conn.tools.get(tool) if conn and conn.status == "connected" else None
        attempts, result, error = 0, None, None

        async def finish(status: str, decision: str, reason: str | None = None, is_write: bool = False) -> dict:
            latency = int((time.perf_counter() - t0) * 1000)
            TOOL_CALLS.labels(server, tool, status).inc()
            TOOL_LATENCY.labels(server).observe(latency / 1000)
            await db.execute(
                """INSERT INTO tool_executions(run_id, workspace_id, actor, server, tool, args, status, decision, error,
                   latency_ms, attempt, node, agent, is_write, origin) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (ctx.run_id, ctx.workspace_id, ctx.principal.subject, server, tool, db.J(redact(args)), status, decision,
                 reason if status != "ok" else None, latency, max(attempts, 1), ctx.node, ctx.agent, is_write, ctx.origin))
            if ctx.run_id:
                await db.execute("UPDATE runs SET tool_calls = tool_calls + 1 WHERE id=%s", (ctx.run_id,))
            out = {"status": status, "server": server, "tool": tool, "latency_ms": latency, "attempts": max(attempts, 1),
                   "decision": decision, "is_write": is_write}
            if status == "ok":
                out["result"] = result
            else:
                out["reason"] = reason
            ctx.emit({"type": "tool_call", "server": server, "tool": tool, "args": redact(args), "status": status,
                      "decision": decision, "reason": reason, "latency_ms": latency, "attempts": max(attempts, 1),
                      "is_write": is_write, "simulated": bool(conn and conn.cfg["kind"] == "demo"),
                      "result_preview": redact(result, 600) if status == "ok" else None})
            return out

        if info is None:
            reason = f"MCP server '{server}' unavailable" if not conn or conn.status != "connected" else f"unknown tool '{tool}'"
            await audit.append({"workspace_id": ctx.workspace_id, "actor": ctx.principal.subject, "action": "tool.call",
                                "resource": f"{server}.{tool}", "decision": "deny", "run_id": ctx.run_id, "detail": {"reason": reason}})
            return await finish("failed" if not conn or conn.status != "connected" else "denied", "deny", reason)

        schema = dict(info.input_schema)
        try:
            jsonschema.validate(args, schema)
        except jsonschema.ValidationError as e:
            return await finish("failed", "invalid_arguments", f"invalid arguments: {e.message}")

        decision = await self.evaluate(ctx, server, tool, args, approved=bool(approval_id))
        if decision.effect == "allow" and decision.is_write:
            problem = await self._verify_approval(ctx, approval_id, server, tool, args)
            if problem:
                decision.effect, decision.reason = "deny", f"approval verification failed: {problem}"
        await audit.append({"workspace_id": ctx.workspace_id, "actor": ctx.principal.subject, "action": "tool.call",
                            "resource": f"{server}.{tool}", "decision": decision.effect, "run_id": ctx.run_id,
                            "detail": {"reason": decision.reason, "risk": decision.risk, "args": args}})
        if decision.effect != "allow":
            return await finish("denied" if decision.effect == "deny" else "approval_required", decision.effect,
                                decision.reason, decision.is_write)

        if ctx.run_id:
            used = await db.fetch_val("SELECT tool_calls FROM runs WHERE id=%s", (ctx.run_id,)) or 0
            if used >= settings.max_tool_calls_per_run:
                return await finish("denied", "budget_exceeded", "tool call budget for this run is exhausted", decision.is_write)

        call_args = dict(args)
        if conn.cfg["kind"] == "demo":  # trusted context for simulated providers; never taken from model output
            call_args["context"] = {"workspace_id": ctx.workspace_id, "actor": ctx.principal.email or ctx.principal.subject,
                                    "idempotency_key": idempotency_key, "dry_run": ctx.dry_run}
        max_attempts = 1 if decision.is_write else 1 + settings.tool_read_retries
        fault = (ctx.options or {}).get("fault_injection") if settings.allow_fault_injection else None
        with span("mcp.tool", **{"mcp.server": server, "mcp.tool": tool, "run.id": ctx.run_id}):
            while attempts < max_attempts:
                attempts += 1
                try:
                    if fault and fault.get("tool") == f"{server}.{tool}" and ctx.run_id:
                        key = (ctx.run_id, fault["tool"])
                        if _fault_counts[key] < int(fault.get("times", 1)):
                            _fault_counts[key] += 1
                            raise ToolError("simulated transient fault (fault injection enabled for this run)")
                    result = await conn.call(tool, call_args, settings.tool_timeout_seconds)
                    error = None
                    break
                except TimeoutError:
                    error = f"timed out after {settings.tool_timeout_seconds}s"
                except (ToolError, ConnectionError) as e:
                    error = str(e)
                except Exception as e:  # transport failure
                    error = f"{type(e).__name__}: {e}"
                if attempts < max_attempts:
                    ctx.emit({"type": "tool_retry", "server": server, "tool": tool, "attempt": attempts, "error": error})
                    await asyncio.sleep(0.2 * attempts)
        await audit.append({"workspace_id": ctx.workspace_id, "actor": ctx.principal.subject, "action": "tool.result",
                            "resource": f"{server}.{tool}", "decision": "success" if error is None else "failure",
                            "run_id": ctx.run_id, "detail": {"error": error, "attempts": attempts}})
        if error is not None:
            return await finish("failed", "allow", error, decision.is_write)
        return await finish("ok", "allow", None, decision.is_write)


gateway = ToolGateway()


async def call_tool(ctx: RunCtx, server: str, tool: str, args: dict | None = None, **kw: Any) -> dict:
    return await gateway.call(ctx, server, tool, args or {}, **kw)
