from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import db
from app.api.deps import not_found
from app.audit import audit
from app.config import settings
from app.graph.build import topology
from app.graph.events import bus
from app.graph.planner import WORKFLOWS
from app.graph.runner import decide_approval, runner
from app.security import Principal, principal, require

router = APIRouter(prefix="/api", tags=["runs"])
RUN_COLS = """id, workspace_id, user_id, conversation_id, request, workflow, intent, status, origin, options, plan, error,
              retry_count, tokens_in, tokens_out, cost_usd, tool_calls, pause_requested, created_at, started_at, finished_at"""


class FaultInjection(BaseModel):
    node: str | None = None
    tool: str | None = None
    times: int = Field(1, ge=1, le=5)


class RunOptions(BaseModel):
    dry_run: bool = False
    weeks: int = Field(12, ge=4, le=26)
    meeting_id: str | None = None
    retrieval_mode: str | None = Field(None, pattern="^(hybrid|vector|keyword)$")
    fault_injection: FaultInjection | None = None


class RunCreate(BaseModel):
    request: str | None = Field(None, max_length=4000)
    workflow: str | None = None
    conversation_id: str | None = None
    options: RunOptions = RunOptions()


def _options(o: RunOptions) -> dict:
    out = o.model_dump(exclude_none=True)
    if o.fault_injection and not settings.allow_fault_injection:
        raise HTTPException(400, "fault injection is disabled in this environment")
    return out


async def _run(run_id: str, p: Principal) -> dict:
    run = await runner.get(run_id, p.tenant)
    if not run:
        raise not_found("run")
    return run


def _public(run: dict) -> dict:
    run = dict(run)
    run.pop("principal", None)
    run["cost_usd"] = float(run.get("cost_usd") or 0)
    if run.get("started_at"):
        end = run.get("finished_at")
        run["duration_ms"] = int((end - run["started_at"]).total_seconds() * 1000) if end else None
    return run


@router.get("/workflows")
async def workflows(p: Principal = Depends(principal)):
    return [{"id": k, **v} for k, v in WORKFLOWS.items()]


@router.get("/graph/topology")
async def graph_topology(p: Principal = Depends(principal)):
    return topology()


@router.post("/runs", status_code=201)
async def create_run(body: RunCreate, p: Principal = Depends(require("agent:run"))):
    if body.workflow and body.workflow not in WORKFLOWS:
        raise HTTPException(400, "unknown workflow")
    request = (body.request or "").strip() or (WORKFLOWS[body.workflow]["prompt"] if body.workflow else "")
    if not request:
        raise HTTPException(400, "request or workflow is required")
    if body.conversation_id and not await db.fetch_one("SELECT 1 FROM conversations WHERE id=%s AND workspace_id=%s",
                                                       (body.conversation_id, p.tenant)):
        raise not_found("conversation")
    return _public(await runner.start(p, request, body.workflow, _options(body.options), body.conversation_id))


@router.get("/runs")
async def list_runs(p: Principal = Depends(principal), status: str | None = None, workflow: str | None = None,
                    limit: int = Query(50, ge=1, le=200), include_eval: bool = False):
    where, params = ["workspace_id=%s"], [p.tenant]
    if status:
        where.append("status=%s")
        params.append(status)
    if workflow:
        where.append("workflow=%s")
        params.append(workflow)
    if not include_eval:
        where.append("origin <> 'eval'")
    rows = await db.fetch_all(f"SELECT {RUN_COLS}, result->>'title' AS title, (result->'evaluation'->>'overall')::float AS score "
                              f"FROM runs WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT %s", (*params, limit))
    return [_public(r) for r in rows]


@router.get("/runs/{run_id}")
async def get_run(run_id: str, p: Principal = Depends(principal)):
    run = _public(await _run(run_id, p))
    run["approvals"] = await db.fetch_all("SELECT * FROM approvals WHERE run_id=%s ORDER BY round, created_at, id", (run_id,))
    run["active"] = run_id in runner.tasks
    return run


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, after: int = 0, p: Principal = Depends(principal)):
    await _run(run_id, p)
    return await db.fetch_all(
        "SELECT seq, ts, type, node, agent, payload FROM run_events WHERE run_id=%s AND workspace_id=%s AND seq>%s ORDER BY seq",
        (run_id, p.tenant, after))


@router.get("/runs/{run_id}/stream")
async def run_stream(run_id: str, request: Request, after: int = 0, p: Principal = Depends(principal)):
    """Server-sent events. Events are persisted first, so a client can reconnect with ?after=<seq> without loss."""
    await _run(run_id, p)
    last = request.headers.get("last-event-id")
    start = int(last) if last and last.isdigit() else after

    async def gen():
        async for ev in bus.stream(run_id, p.tenant, start):
            if await request.is_disconnected():
                break
            if ev is None:
                yield ": keep-alive\n\n"
            else:
                yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/runs/{run_id}/state")
async def run_state(run_id: str, p: Principal = Depends(principal)):
    run = await _run(run_id, p)
    st = await runner.state(run)
    v = st["values"]
    return {"next": st["next"], "checkpoint_id": st["checkpoint_id"], "interrupts": st["interrupts"],
            "values": {k: v.get(k) for k in ("intent", "plan", "cursor", "route", "replans", "revisions", "approval_round",
                                             "evidence", "findings", "proposed_actions", "action_results", "guardrails",
                                             "errors", "evaluation", "analytics", "extracted") if k in v}}


@router.get("/runs/{run_id}/checkpoints")
async def run_checkpoints(run_id: str, p: Principal = Depends(principal)):
    return await runner.history(await _run(run_id, p))


@router.get("/runs/{run_id}/telemetry")
async def run_telemetry(run_id: str, p: Principal = Depends(principal)):
    await _run(run_id, p)
    return {
        "tools": await db.fetch_all("SELECT id, server, tool, args, status, decision, error, latency_ms, attempt, node, agent, is_write, created_at "
                                    "FROM tool_executions WHERE run_id=%s ORDER BY id", (run_id,)),
        "llm": await db.fetch_all("SELECT id, node, agent, provider, model, purpose, prompt_tokens, completion_tokens, cost_usd::float AS cost_usd, "
                                  "latency_ms, estimated, created_at FROM llm_calls WHERE run_id=%s ORDER BY id", (run_id,)),
        "actions": await db.fetch_all("SELECT idempotency_key, action_id, approval_id, server, tool, status, attempts, result, error, created_at, finished_at "
                                      "FROM action_executions WHERE run_id=%s ORDER BY created_at", (run_id,)),
    }


async def _control(run_id: str, p: Principal, action: str, allowed: tuple[str, ...]) -> dict:
    run = await _run(run_id, p)
    if run["status"] not in allowed:
        raise HTTPException(409, f"cannot {action} a run that is {run['status']}")
    await audit.append({"workspace_id": p.tenant, "actor": p.subject, "action": f"run.{action}", "resource": run_id,
                        "decision": "allow", "run_id": run_id})
    return run


@router.post("/runs/{run_id}/pause")
async def pause_run(run_id: str, p: Principal = Depends(require("agent:run"))):
    await runner.pause(await _control(run_id, p, "pause", ("running", "queued")))
    return {"ok": True, "detail": "the run pauses at the next supervisor step"}


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, p: Principal = Depends(require("agent:run"))):
    await runner.resume(await _control(run_id, p, "resume", ("paused",)))
    return {"ok": True}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, p: Principal = Depends(require("agent:run"))):
    await runner.cancel(await _control(run_id, p, "cancel", ("running", "queued", "paused", "waiting_approval")))
    return {"ok": True}


@router.post("/runs/{run_id}/retry")
async def retry_run(run_id: str, p: Principal = Depends(require("agent:run"))):
    await runner.retry(await _control(run_id, p, "retry", ("failed",)))
    return {"ok": True, "detail": "resumed from the last checkpoint"}


# ---------------------------------------------------------------- conversations
class ConversationCreate(BaseModel):
    title: str = Field("New conversation", max_length=120)


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)
    workflow: str | None = None
    options: RunOptions = RunOptions()


@router.get("/conversations")
async def conversations(p: Principal = Depends(principal)):
    return await db.fetch_all(
        """SELECT c.*, (SELECT count(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count,
                  (SELECT status FROM runs r WHERE r.conversation_id=c.id ORDER BY created_at DESC LIMIT 1) AS last_run_status
           FROM conversations c WHERE workspace_id=%s AND user_id=%s ORDER BY updated_at DESC LIMIT 100""", (p.tenant, p.subject))


@router.post("/conversations", status_code=201)
async def create_conversation(body: ConversationCreate, p: Principal = Depends(require("agent:run"))):
    cid = db.new_id("cnv")
    await db.execute("INSERT INTO conversations(id, workspace_id, user_id, title) VALUES (%s,%s,%s,%s)",
                     (cid, p.tenant, p.subject, body.title.strip() or "New conversation"))
    return await db.fetch_one("SELECT * FROM conversations WHERE id=%s", (cid,))


async def _conv(cid: str, p: Principal) -> dict:
    c = await db.fetch_one("SELECT * FROM conversations WHERE id=%s AND workspace_id=%s AND user_id=%s", (cid, p.tenant, p.subject))
    if not c:
        raise not_found("conversation")
    return c


@router.get("/conversations/{cid}")
async def get_conversation(cid: str, p: Principal = Depends(principal)):
    c = await _conv(cid, p)
    c["messages"] = await db.fetch_all(
        """SELECT m.id, m.role, m.content, m.run_id, m.created_at, r.status AS run_status,
                  CASE WHEN m.role='assistant' THEN r.result END AS result
           FROM messages m LEFT JOIN runs r ON r.id=m.run_id WHERE m.conversation_id=%s ORDER BY m.id""", (cid,))
    c["runs"] = [_public(r) for r in await db.fetch_all(
        f"SELECT {RUN_COLS} FROM runs WHERE conversation_id=%s ORDER BY created_at", (cid,))]
    return c


@router.delete("/conversations/{cid}", status_code=204)
async def delete_conversation(cid: str, p: Principal = Depends(require("agent:run"))):
    await _conv(cid, p)
    await db.execute("DELETE FROM conversations WHERE id=%s", (cid,))


@router.post("/conversations/{cid}/messages", status_code=201)
async def post_message(cid: str, body: MessageCreate, p: Principal = Depends(require("agent:run"))):
    c = await _conv(cid, p)
    if body.workflow and body.workflow not in WORKFLOWS:
        raise HTTPException(400, "unknown workflow")
    if c["title"] == "New conversation":
        await db.execute("UPDATE conversations SET title=%s WHERE id=%s", (body.content.strip()[:60], cid))
    return _public(await runner.start(p, body.content.strip(), body.workflow, _options(body.options), cid))


# ---------------------------------------------------------------- approvals
class Decision(BaseModel):
    decision: str = Field(..., pattern="^(approve|reject|request_changes|edit)$")
    comment: str = Field("", max_length=2000)
    edited_params: dict | None = None


@router.get("/approvals")
async def approvals(p: Principal = Depends(principal), status: str | None = None, limit: int = Query(100, ge=1, le=300)):
    where, params = ["a.workspace_id=%s"], [p.tenant]
    if status == "decided":
        where.append("a.status <> 'pending'")
    elif status:
        where.append("a.status=%s")
        params.append(status)
    return await db.fetch_all(
        f"""SELECT a.*, r.request AS run_request, r.workflow, r.status AS run_status, r.origin AS run_origin, u.name AS requested_by
            FROM approvals a JOIN runs r ON r.id=a.run_id LEFT JOIN users u ON u.id=r.user_id
            WHERE {' AND '.join(where)} ORDER BY (a.status='pending') DESC, a.created_at DESC, a.id LIMIT %s""", (*params, limit))


@router.get("/approvals/{approval_id}")
async def approval(approval_id: str, p: Principal = Depends(principal)):
    a = await db.fetch_one(
        """SELECT a.*, r.request AS run_request, r.workflow, r.status AS run_status, u.name AS requested_by
           FROM approvals a JOIN runs r ON r.id=a.run_id LEFT JOIN users u ON u.id=r.user_id
           WHERE a.id=%s AND a.workspace_id=%s""", (approval_id, p.tenant))
    if not a:
        raise not_found("approval")
    a["audit"] = await db.fetch_all(
        "SELECT seq, ts, actor, action, decision, detail FROM audit_events WHERE workspace_id=%s AND (resource=%s OR (run_id=%s AND action LIKE 'approval.%%')) ORDER BY seq",
        (p.tenant, approval_id, a["run_id"]))
    a["execution"] = await db.fetch_one("SELECT status, attempts, result, error, finished_at FROM action_executions WHERE approval_id=%s",
                                        (approval_id,))
    return a


@router.post("/approvals/{approval_id}/decision")
async def decide(approval_id: str, body: Decision, p: Principal = Depends(require("approval:decide"))):
    try:
        return await decide_approval(p, approval_id, body.decision, body.comment, body.edited_params)
    except LookupError:
        raise not_found("approval") from None
    except ValueError as e:
        raise HTTPException(409 if "already" in str(e) or "concurrently" in str(e) else 400, str(e)) from None
