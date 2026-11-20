"""First-start demo activity.

Nothing here is fabricated telemetry: each item is a real execution of the LangGraph graph / evaluation harness against
the synthetic demo data, tagged origin='seed' so the UI can label it. One run is left waiting for approval so the
Approval Center has genuine pending decisions.
"""
from __future__ import annotations

import structlog

from app import db
from app.evals import runner as evals
from app.graph.planner import WORKFLOWS
from app.graph.runner import decide_approval, runner
from app.security import ROLE_SCOPES, Principal

log = structlog.get_logger()


def _p(uid: str, role: str, name: str, email: str, ws: str = "meridian") -> Principal:
    return Principal(uid, ws, ROLE_SCOPES[role], role, name, email)


async def seed_history(ws: str = "meridian") -> bool:
    if await db.fetch_val("SELECT count(*) FROM runs WHERE workspace_id=%s", (ws,)):
        return False
    operator = _p("usr_operator", "operator", "Jordan Blake", "operator@meridian.example")
    approver = _p("usr_approver", "approver", "Priya Raman", "approver@meridian.example")
    admin = _p("usr_admin", "admin", "Avery Chen", "admin@meridian.example")

    async def conv(title: str) -> str:
        cid = db.new_id("cnv")
        await db.execute("INSERT INTO conversations(id, workspace_id, user_id, title) VALUES (%s,%s,%s,%s)",
                         (cid, ws, operator.subject, title))
        return cid

    # 1. Knowledge research: completes without approvals.
    await runner.start(operator, WORKFLOWS["knowledge_research"]["prompt"], "knowledge_research",
                       conversation_id=await conv("Operational risk research"), origin="seed", wait=True)
    # 2. Meeting to execution: approved end to end by the approver (demo provider records are created).
    run = await runner.start(operator, WORKFLOWS["meeting_to_execution"]["prompt"], "meeting_to_execution",
                             conversation_id=await conv("Project Atlas follow-ups"), origin="seed", wait=True)
    for a in await db.fetch_all("SELECT id FROM approvals WHERE run_id=%s AND status='pending' ORDER BY created_at, id", (run["id"],)):
        await decide_approval(approver, a["id"], "approve", "Approved during demo seeding")
    if run["id"] in runner.tasks:
        await runner.tasks[run["id"]]
    # 3. Retrieval outage with recovery (fault injection): shows a retry and a keyword-only replan in the trace.
    await runner.start(operator, "Why are customers churning and what do we recommend?", "knowledge_research",
                       options={"fault_injection": {"node": "research.retrieve", "times": 2}}, origin="seed", wait=True)
    # 4. Support escalation: left waiting for approval.
    await runner.start(operator, WORKFLOWS["support_escalation"]["prompt"], "support_escalation",
                       conversation_id=await conv("Support escalation review"), origin="seed", wait=True)
    # 5. Baseline evaluations.
    for ds in ("retrieval_quality", "agent_workflows", "safety_guardrails"):
        r = await evals.start(admin, ds, wait=True, origin="seed")
        if r["status"] == "completed":
            await evals.set_baseline(r["id"], ws)
    log.info("demo_history_seeded", workspace=ws)
    return True
