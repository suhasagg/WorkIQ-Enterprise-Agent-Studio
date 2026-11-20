"""Durable run lifecycle: start, stream, interrupt (approval/pause), resume, cancel, retry and restart recovery.

Graph state is checkpointed to PostgreSQL after every super-step (thread_id == run_id), so a run can be resumed by
any process after an approval, a pause, a failure or a process restart.
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from app import db
from app.audit import audit
from app.config import settings
from app.graph.build import build_graph
from app.graph.events import bus
from app.metrics import RUNS
from app.redaction import find_sensitive, redact_text
from app.security import Principal

log = structlog.get_logger()
TERMINAL = ("completed", "failed", "cancelled")


class Runner:
    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task] = {}
        self._graph = None
        self._loop = None
        self.stopping = False

    async def graph(self):
        loop = asyncio.get_running_loop()
        if self._graph is None or self._loop is not loop:
            saver = AsyncPostgresSaver(await db.checkpoint_pool())
            await saver.setup()
            self._graph, self._loop, self.tasks, self.stopping = build_graph().compile(checkpointer=saver), loop, {}, False
        return self._graph

    @staticmethod
    def config(run: dict) -> dict:
        return {"configurable": {"thread_id": run["id"], "run_id": run["id"], "principal": run["principal"],
                                 "options": run["options"] or {}, "origin": run["origin"]},
                "recursion_limit": 60}

    async def create(self, p: Principal, request: str, workflow: str | None = None, options: dict | None = None,
                     conversation_id: str | None = None, origin: str = "live") -> dict:
        run_id = db.new_id("run")
        request = redact_text(request)  # secrets are never persisted; the graph's intake node redacts its own copy
        if conversation_id:
            await db.execute("INSERT INTO messages(conversation_id, workspace_id, role, content, run_id) VALUES (%s,%s,'user',%s,%s)",
                             (conversation_id, p.tenant, request, run_id))
            await db.execute("UPDATE conversations SET updated_at=now() WHERE id=%s", (conversation_id,))
        await db.execute(
            """INSERT INTO runs(id, workspace_id, user_id, conversation_id, request, workflow, status, origin, options, principal)
               VALUES (%s,%s,%s,%s,%s,%s,'queued',%s,%s,%s)""",
            (run_id, p.tenant, p.subject, conversation_id, request, workflow, origin, db.J(options or {}), db.J(p.to_dict())))
        await audit.append({"workspace_id": p.tenant, "actor": p.subject, "action": "run.create", "resource": run_id,
                            "decision": "allow", "run_id": run_id, "detail": {"workflow": workflow, "request": request[:300]}})
        return await self.get(run_id, p.tenant)

    async def get(self, run_id: str, workspace_id: str) -> dict | None:
        return await db.fetch_one("SELECT * FROM runs WHERE id=%s AND workspace_id=%s", (run_id, workspace_id))

    def launch(self, run: dict, graph_input: Any) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(self._drive(run, graph_input), name=f"run:{run['id']}")
        self.tasks[run["id"]] = task
        task.add_done_callback(lambda t, rid=run["id"]: self.tasks.pop(rid, None) if self.tasks.get(rid) is t else None)
        return task

    async def start(self, p: Principal, request: str, workflow: str | None = None, options: dict | None = None,
                    conversation_id: str | None = None, origin: str = "live", wait: bool = False) -> dict:
        await self.graph()
        run = await self.create(p, request, workflow, options, conversation_id, origin)
        # Only the redacted request enters graph state, so secrets never reach checkpoints, events or the model.
        leaks = find_sensitive(request)
        flagged = [{"type": "sensitive_data_in_request", "action": "redacted before processing", "flags": leaks}] if leaks else []
        task = self.launch(run, {"run_id": run["id"], "request": run["request"], "workflow": workflow or "", "guardrails": flagged})
        if wait:
            await task
        return await self.get(run["id"], p.tenant)

    async def _set(self, run_id: str, status: str, **fields: Any) -> None:
        cols = "".join(f", {k}=%s" for k in fields)
        fin = ", finished_at=now()" if status in TERMINAL else ""
        await db.execute(f"UPDATE runs SET status=%s, updated_at=now(){fin}{cols} WHERE id=%s", (status, *fields.values(), run_id))

    async def _drive(self, run: dict, graph_input: Any) -> None:
        run_id, ws = run["id"], run["workspace_id"]
        emit = bus.emitter(run_id, ws)
        graph = await self.graph()
        cfg = self.config(run)
        try:
            await db.execute("UPDATE runs SET status='running', started_at=COALESCE(started_at, now()), error=NULL, updated_at=now() WHERE id=%s",
                             (run_id,))
            emit({"type": "run_resumed" if graph_input is None or isinstance(graph_input, Command) else "run_started",
                  "workflow": run.get("workflow"), "request": run["request"][:300]})
            async with asyncio.timeout(settings.run_timeout_seconds):
                async for _ in graph.astream(graph_input, cfg, stream_mode="updates"):
                    pass
            snap = await graph.aget_state(cfg)
            interrupts = [i.value for t in snap.tasks for i in t.interrupts]
            if snap.next and interrupts:
                kind = interrupts[0].get("type") if isinstance(interrupts[0], dict) else "pause"
                status = "waiting_approval" if kind == "approval" else "paused"
                await self._set(run_id, status)
                emit({"type": "run_" + status, "interrupt": interrupts[0]})
            else:
                final = snap.values.get("final") or {}
                await self._set(run_id, "completed", result=db.J(final))
                if run.get("conversation_id") and final:
                    await db.execute("INSERT INTO messages(conversation_id, workspace_id, role, content, run_id) VALUES (%s,%s,'assistant',%s,%s)",
                                     (run["conversation_id"], ws, final.get("markdown", ""), run_id))
                RUNS.labels(snap.values.get("intent") or "unknown", "completed").inc()
                emit({"type": "run_completed", "overall": (final.get("evaluation") or {}).get("overall"), "partial": final.get("partial")})
        except asyncio.CancelledError:
            if not self.stopping:  # on process shutdown the run stays 'running' and is recovered at next start
                await asyncio.shield(self._cancelled(run_id, emit))
        except Exception as e:
            msg = f"run exceeded {settings.run_timeout_seconds}s" if isinstance(e, TimeoutError) else f"{type(e).__name__}: {e}"
            log.exception("run_failed", run_id=run_id)
            await self._set(run_id, "failed", error=msg[:1000])
            RUNS.labels(run.get("workflow") or "unknown", "failed").inc()
            emit({"type": "run_failed", "error": msg[:500]})
        finally:
            await asyncio.shield(bus.flush())

    async def _cancelled(self, run_id: str, emit) -> None:
        await self._set(run_id, "cancelled")
        emit({"type": "run_cancelled"})
        await bus.flush()

    # ------------------------------------------------------------ controls
    async def resume(self, run: dict, value: Any = True) -> None:
        """Resume an interrupted run (after approvals are decided or after a pause)."""
        if run["id"] in self.tasks:
            return
        await db.execute("UPDATE runs SET pause_requested=false WHERE id=%s", (run["id"],))
        self.launch(run, Command(resume=value))

    async def pause(self, run: dict) -> None:
        await db.execute("UPDATE runs SET pause_requested=true WHERE id=%s", (run["id"],))
        bus.emit(run["id"], run["workspace_id"], {"type": "pause_requested"})

    async def cancel(self, run: dict) -> None:
        task = self.tasks.get(run["id"])
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            await db.execute("UPDATE approvals SET status='cancelled' WHERE run_id=%s AND status='pending'", (run["id"],))
            await self._cancelled(run["id"], bus.emitter(run["id"], run["workspace_id"]))
        await db.execute("UPDATE approvals SET status='cancelled' WHERE run_id=%s AND status='pending'", (run["id"],))

    async def retry(self, run: dict) -> None:
        """Retry a failed run from its last checkpoint (completed nodes are not re-executed)."""
        if run["id"] in self.tasks:
            return
        await db.execute("UPDATE runs SET retry_count=retry_count+1, finished_at=NULL WHERE id=%s", (run["id"],))
        graph = await self.graph()
        snap = await graph.aget_state(self.config(run))
        has_checkpoint = bool(snap.values)
        self.launch(run, None if has_checkpoint else {"run_id": run["id"], "request": run["request"], "workflow": run["workflow"] or ""})

    async def recover(self) -> list[str]:
        """After a process restart, continue runs that were executing when the process died."""
        await self.graph()
        rows = await db.fetch_all("SELECT * FROM runs WHERE status IN ('running','queued') AND origin='live' ORDER BY created_at")
        recovered = []
        for run in rows:
            if run["id"] in self.tasks:
                continue
            snap = await (await self.graph()).aget_state(self.config(run))
            bus.emit(run["id"], run["workspace_id"], {"type": "run_recovered", "from_checkpoint": bool(snap.values),
                                                      "next": list(snap.next)})
            self.launch(run, None if snap.values else {"run_id": run["id"], "request": run["request"], "workflow": run["workflow"] or ""})
            recovered.append(run["id"])
        return recovered

    async def state(self, run: dict) -> dict:
        graph = await self.graph()
        snap = await graph.aget_state(self.config(run))
        return {"values": snap.values, "next": list(snap.next),
                "checkpoint_id": (snap.config or {}).get("configurable", {}).get("checkpoint_id"),
                "interrupts": [i.value for t in snap.tasks for i in t.interrupts]}

    async def history(self, run: dict, limit: int = 60) -> list[dict]:
        graph = await self.graph()
        out = []
        async for s in graph.aget_state_history(self.config(run), limit=limit):
            out.append({"checkpoint_id": s.config["configurable"].get("checkpoint_id"), "step": (s.metadata or {}).get("step"),
                        "source": (s.metadata or {}).get("source"), "next": list(s.next),
                        "created_at": s.created_at})
        return out

    async def shutdown(self) -> None:
        """Graceful shutdown: stop driving runs but leave them 'running' so the next process recovers them
        from their last checkpoint."""
        self.stopping = True
        tasks = [t for t in self.tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks = {}
        await bus.flush()


runner = Runner()


async def decide_approval(p: Principal, approval_id: str, decision: str, comment: str = "", edited_params: dict | None = None) -> dict:
    """Record a human decision. Resumes the run once every approval of the current round is decided."""
    from app.mcp.gateway import params_hash
    a = await db.fetch_one("SELECT * FROM approvals WHERE id=%s AND workspace_id=%s", (approval_id, p.tenant))
    if not a:
        raise LookupError("approval not found")
    if a["status"] != "pending":
        raise ValueError(f"approval already {a['status']}")
    status = {"approve": "approved", "reject": "rejected", "request_changes": "changes_requested", "edit": "edited"}[decision]
    if status == "edited":
        if not isinstance(edited_params, dict) or not edited_params:
            raise ValueError("edited_params is required for an edit decision")
    if status in ("rejected", "changes_requested") and not comment.strip():
        raise ValueError("a comment is required when rejecting or requesting changes")
    n = await db.execute(
        """UPDATE approvals SET status=%s, comment=%s, edited_params=%s, params_hash=%s, decided_by=%s, decided_by_name=%s,
           decided_at=now() WHERE id=%s AND status='pending'""",
        (status, comment, db.J(edited_params) if status == "edited" else None,
         params_hash(edited_params) if status == "edited" else a["params_hash"], p.subject, p.name or p.email, approval_id))
    if not n:
        raise ValueError("approval was decided concurrently")
    await audit.append({"workspace_id": p.tenant, "actor": p.subject, "action": "approval.decide", "resource": approval_id,
                        "decision": status, "run_id": a["run_id"],
                        "detail": {"tool": f"{a['server']}.{a['tool']}", "comment": comment, "edited": status == "edited"}})
    remaining = await db.fetch_val("SELECT count(*) FROM approvals WHERE run_id=%s AND status='pending'", (a["run_id"],))
    run = await runner.get(a["run_id"], p.tenant)
    resumed = False
    if remaining == 0 and run and run["status"] == "waiting_approval":
        await runner.resume(run, {"decided_by": p.subject})
        resumed = True
    return {**(await db.fetch_one("SELECT * FROM approvals WHERE id=%s", (approval_id,))), "run_resumed": resumed, "remaining": remaining}
