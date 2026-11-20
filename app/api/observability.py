from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app import db, telemetry
from app.audit import audit
from app.config import settings
from app.llm.model import model_info
from app.mcp.manager import mcp_manager
from app.memory import memory
from app.rag.embeddings import embedder
from app.security import Principal, principal, require

router = APIRouter(prefix="/api", tags=["observability"])


def _since(hours: int) -> str:
    return f"now() - interval '{int(hours)} hours'"


@router.get("/system")
async def system(p: Principal = Depends(principal)):
    return {"demo_mode": settings.demo_mode, "env": settings.env, "model": model_info(),
            "embeddings": {"model": embedder.model_name, "dim": settings.embedding_dim},
            "telemetry": telemetry.status(), "fault_injection": settings.allow_fault_injection,
            "limits": {"max_tool_calls_per_run": settings.max_tool_calls_per_run, "run_timeout_seconds": settings.run_timeout_seconds,
                       "node_timeout_seconds": settings.node_timeout_seconds, "tool_timeout_seconds": settings.tool_timeout_seconds},
            "notice": "Demo mode: every provider is a local simulated MCP server with synthetic data."
                      if settings.demo_mode else None}


@router.get("/dashboard")
async def dashboard(p: Principal = Depends(principal), hours: int = Query(168, ge=1, le=2160)):
    ws, since = p.tenant, _since(hours)
    runs = await db.fetch_one(
        f"""SELECT count(*) AS total, count(*) FILTER (WHERE status='completed') AS completed,
                   count(*) FILTER (WHERE status='failed') AS failed, count(*) FILTER (WHERE status='cancelled') AS cancelled,
                   count(*) FILTER (WHERE status IN ('running','queued')) AS active,
                   count(*) FILTER (WHERE status IN ('waiting_approval','paused')) AS waiting,
                   COALESCE(round(avg(extract(epoch FROM finished_at-started_at)*1000) FILTER (WHERE status='completed')),0)::int AS avg_duration_ms,
                   COALESCE(sum(tokens_in+tokens_out),0)::int AS tokens, COALESCE(sum(cost_usd),0)::float AS cost_usd,
                   COALESCE(round(avg((result->'evaluation'->>'overall')::numeric) FILTER (WHERE status='completed'),3),0)::float AS avg_quality
            FROM runs WHERE workspace_id=%s AND origin <> 'eval' AND created_at > {since}""", (ws,))
    done = runs["completed"] + runs["failed"]
    runs["success_rate"] = round(runs["completed"] / done, 3) if done else None
    tools = await db.fetch_one(
        f"""SELECT count(*) AS total, count(*) FILTER (WHERE status='ok') AS ok, count(*) FILTER (WHERE status='failed') AS failed,
                   count(*) FILTER (WHERE status='denied') AS denied, count(*) FILTER (WHERE status='approval_required') AS held
            FROM tool_executions WHERE workspace_id=%s AND origin <> 'eval' AND created_at > {since}""", (ws,))
    executed = tools["ok"] + tools["failed"]
    tools["success_rate"] = round(tools["ok"] / executed, 3) if executed else None
    approvals = await db.fetch_one(
        """SELECT count(*) FILTER (WHERE status='pending') AS pending, count(*) FILTER (WHERE status IN ('approved','edited')) AS approved,
                  count(*) FILTER (WHERE status='rejected') AS rejected, count(*) FILTER (WHERE status='changes_requested') AS changes_requested
           FROM approvals WHERE workspace_id=%s""", (ws,))
    agents = await db.fetch_all(
        f"""SELECT e.agent, count(*) FILTER (WHERE e.type='node_start') AS activations,
                   count(*) FILTER (WHERE e.type='node_end' AND e.payload->>'status'='error') AS errors,
                   COALESCE(round(avg((e.payload->>'duration_ms')::numeric) FILTER (WHERE e.type='node_end')),0)::int AS avg_ms,
                   max(e.ts) AS last_active
            FROM run_events e JOIN runs r ON r.id=e.run_id
            WHERE e.workspace_id=%s AND e.agent IS NOT NULL AND r.origin <> 'eval' AND e.ts > {since} GROUP BY e.agent ORDER BY 2 DESC""", (ws,))
    busy = {r["agent"] for r in await db.fetch_all(
        """SELECT DISTINCT ON (e.run_id) e.agent FROM run_events e JOIN runs r ON r.id=e.run_id
           WHERE r.workspace_id=%s AND r.status='running' AND e.type='node_start' ORDER BY e.run_id, e.seq DESC""", (ws,))}
    for a in agents:
        a["state"] = "active" if a["agent"] in busy else "idle"
    recent = await db.fetch_all(
        """SELECT id, request, workflow, intent, status, origin, created_at, started_at, finished_at, tool_calls,
                  result->>'title' AS title, (result->'evaluation'->>'overall')::float AS score
           FROM runs WHERE workspace_id=%s AND origin <> 'eval' ORDER BY created_at DESC LIMIT 8""", (ws,))
    activity = await db.fetch_all(
        """SELECT e.seq, e.ts, e.type, e.node, e.agent, e.run_id, e.payload FROM run_events e JOIN runs r ON r.id=e.run_id
           WHERE e.workspace_id=%s AND r.origin <> 'eval'
             AND e.type IN ('run_started','run_completed','run_failed','run_cancelled','run_waiting_approval','run_resumed','approval_decision',
                            'action_executed','guardrail','tool_retry','replan','step_retry','run_recovered','run_paused')
           ORDER BY e.seq DESC LIMIT 25""", (ws,))
    servers = [c.public() for c in mcp_manager.connections.values()]
    docs = await db.fetch_one("SELECT count(*) AS documents, COALESCE(sum(chunk_count),0)::int AS chunks FROM documents WHERE workspace_id=%s", (ws,))
    return {"window_hours": hours, "runs": runs, "tools": tools, "approvals": approvals, "agents": agents, "recent_runs": recent,
            "activity": activity, "knowledge": docs,
            "mcp": {"total": len(servers), "connected": sum(s["status"] == "connected" for s in servers),
                    "servers": [{k: s[k] for k in ("id", "name", "kind", "status", "tool_count", "last_ping_ms")} for s in servers]},
            "model": model_info()}


@router.get("/observability")
async def observability(p: Principal = Depends(principal), hours: int = Query(168, ge=1, le=2160), include_eval: bool = False):
    ws, since = p.tenant, _since(hours)
    of = "" if include_eval else "AND origin <> 'eval'"
    rof = "" if include_eval else "AND r.origin <> 'eval'"
    bucket = "hour" if hours <= 48 else "day"
    series = await db.fetch_all(
        f"""SELECT date_trunc('{bucket}', created_at) AS t, count(*) AS runs, count(*) FILTER (WHERE status='completed') AS completed,
                   count(*) FILTER (WHERE status='failed') AS failed,
                   COALESCE(round(avg(extract(epoch FROM finished_at-started_at)*1000) FILTER (WHERE finished_at IS NOT NULL)),0)::int AS avg_ms,
                   COALESCE(sum(tokens_in+tokens_out),0)::int AS tokens, COALESCE(sum(cost_usd),0)::float AS cost_usd
            FROM runs WHERE workspace_id=%s {of} AND created_at > {since} GROUP BY 1 ORDER BY 1""", (ws,))
    latency = await db.fetch_one(
        f"""SELECT COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY d),0)::int AS p50, COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY d),0)::int AS p95,
                   COALESCE(max(d),0)::int AS max, count(*) AS n
            FROM (SELECT extract(epoch FROM finished_at-started_at)*1000 AS d FROM runs
                  WHERE workspace_id=%s {of} AND status='completed' AND created_at > {since}) x""", (ws,))
    nodes = await db.fetch_all(
        f"""SELECT e.node, e.agent, count(*) AS calls, round(avg((e.payload->>'duration_ms')::numeric))::int AS avg_ms,
                   max((e.payload->>'duration_ms')::numeric)::int AS max_ms, count(*) FILTER (WHERE e.payload->>'status'='error') AS errors
            FROM run_events e JOIN runs r ON r.id=e.run_id
            WHERE e.workspace_id=%s AND e.type='node_end' {rof} AND e.ts > {since} GROUP BY 1,2 ORDER BY avg_ms DESC""", (ws,))
    tools = await db.fetch_all(
        f"""SELECT server, tool, count(*) AS calls, count(*) FILTER (WHERE status='ok') AS ok, count(*) FILTER (WHERE status='failed') AS failed,
                   count(*) FILTER (WHERE status='denied') AS denied, count(*) FILTER (WHERE status='approval_required') AS held,
                   COALESCE(round(avg(latency_ms) FILTER (WHERE status='ok')),0)::int AS avg_ms, COALESCE(sum(attempt-1),0)::int AS retries
            FROM tool_executions WHERE workspace_id=%s {of} AND created_at > {since} GROUP BY 1,2 ORDER BY calls DESC""", (ws,))
    llm = await db.fetch_all(
        f"""SELECT provider, model, COALESCE(purpose,'-') AS purpose, count(*) AS calls, sum(prompt_tokens)::int AS prompt_tokens,
                   sum(completion_tokens)::int AS completion_tokens, sum(cost_usd)::float AS cost_usd, round(avg(latency_ms))::int AS avg_ms,
                   bool_or(estimated) AS estimated
            FROM llm_calls WHERE workspace_id=%s {of} AND created_at > {since} GROUP BY 1,2,3 ORDER BY calls DESC""", (ws,))
    failures = await db.fetch_all(
        f"""SELECT e.seq, e.ts, e.type, e.node, e.agent, e.run_id, e.payload FROM run_events e JOIN runs r ON r.id=e.run_id
            WHERE e.workspace_id=%s {rof} AND e.ts > {since}
              AND (e.type IN ('run_failed','step_retry','tool_retry','replan','run_recovered')
                   OR (e.type='node_end' AND e.payload->>'status'='error'))
            ORDER BY e.seq DESC LIMIT 40""", (ws,))
    guardrails = await db.fetch_all(
        f"""SELECT e.payload->>'kind' AS kind, count(*) AS n FROM run_events e JOIN runs r ON r.id=e.run_id
            WHERE e.workspace_id=%s {rof} AND e.type='guardrail' AND e.ts > {since} GROUP BY 1 ORDER BY 2 DESC""", (ws,))
    retrieval = await db.fetch_one(
        f"""SELECT count(*) AS queries, COALESCE(round(avg((e.payload->>'latency_ms')::numeric),1),0)::float AS avg_ms,
                   COALESCE(round(avg((e.payload->>'candidates')::numeric),1),0)::float AS avg_candidates
            FROM run_events e JOIN runs r ON r.id=e.run_id WHERE e.workspace_id=%s {rof} AND e.type='retrieval' AND e.ts > {since}""", (ws,))
    quality = await db.fetch_one(
        f"""SELECT COALESCE(round(avg((result->'evaluation'->'scores'->>'groundedness')::numeric),3),0)::float AS groundedness,
                   COALESCE(round(avg((result->'evaluation'->'scores'->>'completeness')::numeric),3),0)::float AS completeness,
                   COALESCE(round(avg((result->'evaluation'->'scores'->>'tool_success')::numeric),3),0)::float AS tool_success,
                   COALESCE(round(avg((result->'evaluation'->'scores'->>'policy_compliance')::numeric),3),0)::float AS policy_compliance,
                   count(*) AS n
            FROM runs WHERE workspace_id=%s {of} AND status='completed' AND created_at > {since}""", (ws,))
    return {"window_hours": hours, "bucket": bucket, "series": series, "run_latency_ms": latency, "nodes": nodes, "tools": tools,
            "llm": llm, "failures": failures, "guardrails": guardrails, "retrieval": retrieval, "quality": quality,
            "telemetry": telemetry.status(), "model": model_info(),
            "cost_note": "The deterministic demo model makes no paid API calls: cost is $0 and token counts are estimates."
                         if model_info()["provider"] == "deterministic" else "Cost is computed from provider-reported token usage and list prices."}


@router.get("/audit")
async def audit_log(p: Principal = Depends(require("audit:read")), limit: int = Query(200, ge=1, le=1000), run_id: str | None = None):
    return await audit.list(p.tenant, limit, run_id)


@router.get("/audit/verify")
async def audit_verify(p: Principal = Depends(require("audit:read"))):
    return await audit.verify(p.tenant)


@router.get("/memory")
async def memory_snapshot(p: Principal = Depends(principal)):
    return await memory.snapshot(p.tenant, 20)
