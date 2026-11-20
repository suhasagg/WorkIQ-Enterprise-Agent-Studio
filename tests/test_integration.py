"""Integration tests against real PostgreSQL/pgvector, real MCP stdio servers and the compiled LangGraph graph."""
import asyncio

from app import db
from app.context import RunCtx
from app.graph.build import build_graph, topology
from app.graph.runner import runner
from app.mcp.gateway import gateway
from app.mcp.manager import mcp_manager
from app.rag.retrieve import search
from tests.conftest import make_principal


# ---------------------------------------------------------------- auth / RBAC
async def test_health_and_auth(api):
    assert (await api.get(None, "/health/live")).json() == {"status": "ok"}
    ready = (await api.get(None, "/health/ready")).json()
    assert ready["status"] == "ready" and ready["mcp_servers"]["workiq"] == "connected"
    assert (await api.get(None, "/api/runs")).status_code == 401
    assert (await api.post(None, "/api/auth/login", {"email": "admin@meridian.example", "password": "wrong"})).status_code == 401
    me = (await api.get("viewer", "/api/auth/me")).json()
    assert me["role"] == "viewer" and me["scopes"] == ["workiq:read"]


async def test_rbac_enforced_server_side(api):
    assert (await api.post("viewer", "/api/runs", {"workflow": "knowledge_research"})).status_code == 403
    assert (await api.get("operator", "/api/audit")).status_code == 403
    assert (await api.post("operator", "/api/mcp/servers", {"id": "acme", "name": "Acme", "url": "https://x.example/mcp"})).status_code == 403
    assert (await api.call("operator", "PUT", "/api/mcp/servers/tracker/tools/create_issue", json={"enabled": False})).status_code == 403
    assert (await api.get("admin", "/api/audit")).status_code == 200


# ---------------------------------------------------------------- graph structure
async def test_graph_topology_is_real_langgraph(api):
    compiled = build_graph().compile()
    assert {"intake", "supervisor", "research", "analytics", "task", "communication", "compliance", "approval_gate",
            "execute_actions", "synthesizer", "evaluator", "finalize"} <= set(compiled.get_graph().nodes)
    t = topology()
    ids = {n["id"] for n in t["nodes"]}
    assert {"research:retrieve", "analytics:analyze_kpis"} <= ids  # subgraph internals are exposed
    assert any(e["conditional"] for e in t["edges"])
    assert (await api.get("viewer", "/api/graph/topology")).status_code == 200


# ---------------------------------------------------------------- workflows
async def test_knowledge_research_completes_with_citations(api):
    run = await api.run_workflow("knowledge_research")
    assert run["status"] == "completed" and run["intent"] == "knowledge_research"
    full = (await api.get("operator", f"/api/runs/{run['id']}")).json()
    res = full["result"]
    assert res["citations"] and "[1]" in res["markdown"] and res["evaluation"]["scores"]["groundedness"] == 1.0
    assert not full["approvals"]
    events = (await api.get("operator", f"/api/runs/{run['id']}/events")).json()
    types = [e["type"] for e in events]
    assert types[0] == "run_started" and types[-1] == "run_completed" and {"plan", "retrieval", "evaluation"} <= set(types)
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)


async def test_unanswerable_question_abstains(api):
    run = await api.run_workflow(None, "What is the capital expenditure budget for Project Lighthouse in Antarctica?")
    res = (await api.get("operator", f"/api/runs/{run['id']}")).json()["result"]
    assert not res["citations"] and "No sufficiently relevant evidence" in res["markdown"]


async def test_wbr_pauses_for_approval_then_resumes_and_executes(api):
    run = await api.run_workflow("weekly_business_review")
    assert run["status"] == "waiting_approval"
    before = await db.fetch_val("SELECT count(*) FROM demo_records WHERE provider='tracker' AND kind='issue'")
    state = (await api.get("operator", f"/api/runs/{run['id']}/state")).json()
    assert state["next"] == ["approval_gate"] and state["interrupts"][0]["type"] == "approval"
    assert await db.fetch_val("SELECT count(*) FROM action_executions WHERE run_id=%s", (run["id"],)) == 0  # nothing ran yet
    pending = (await api.get("operator", f"/api/runs/{run['id']}")).json()["approvals"]
    assert len(pending) >= 3 and all(a["status"] == "pending" and a["evidence"] is not None for a in pending)
    # operators cannot approve
    assert (await api.post("operator", f"/api/approvals/{pending[0]['id']}/decision", {"decision": "approve"})).status_code == 403
    mail = next(a for a in pending if a["tool"] == "do_action")
    r = await api.post("approver", f"/api/approvals/{mail['id']}/decision", {"decision": "reject", "comment": "not this week"})
    assert r.json()["run_resumed"] is False  # other approvals are still pending
    await api.decide_all(run["id"])
    done = await api.wait_run(run["id"], until=("completed", "failed"))
    assert done["status"] == "completed"
    res = (await api.get("operator", f"/api/runs/{run['id']}")).json()["result"]
    actions = next(s["items"] for s in res["sections"] if s["kind"] == "actions")
    assert sum(a["status"] == "executed" for a in actions) == len(pending) - 1
    assert next(a for a in actions if a["tool"] == "workiq.do_action")["status"] == "rejected"
    assert await db.fetch_val("SELECT count(*) FROM demo_records WHERE provider='tracker' AND kind='issue'") == before + len(pending) - 1
    assert res["evaluation"]["scores"]["policy_compliance"] == 1.0
    # deciding twice is rejected
    assert (await api.post("approver", f"/api/approvals/{mail['id']}/decision", {"decision": "approve"})).status_code == 409
    # approval audit trail
    detail = (await api.get("approver", f"/api/approvals/{pending[1]['id']}")).json()
    assert detail["execution"]["status"] == "completed" and any(e["action"] == "approval.decide" for e in detail["audit"])


async def test_edit_and_request_changes(api):
    run = await api.run_workflow("meeting_to_execution")
    assert run["status"] == "waiting_approval"
    pending = (await api.get("approver", f"/api/runs/{run['id']}")).json()["approvals"]
    task = next(a for a in pending if a["tool"] == "create_issue")
    mail = next(a for a in pending if a["tool"] == "do_action")
    edited = {**task["params"], "title": "EDITED BY REVIEWER"}
    assert (await api.post("approver", f"/api/approvals/{task['id']}/decision", {"decision": "edit"})).status_code == 400
    assert (await api.post("approver", f"/api/approvals/{task['id']}/decision",
                           {"decision": "edit", "edited_params": edited, "comment": "clearer title"})).status_code == 200
    assert (await api.post("approver", f"/api/approvals/{mail['id']}/decision",
                           {"decision": "request_changes", "comment": "mention the beta date"})).status_code == 200
    await api.decide_all(run["id"])
    again = await api.wait_run(run["id"], until=("waiting_approval", "completed", "failed"))
    assert again["status"] == "waiting_approval"  # the revised action needs a fresh approval (round 1)
    round1 = [a for a in (await api.get("approver", f"/api/runs/{run['id']}")).json()["approvals"] if a["status"] == "pending"]
    assert len(round1) == 1 and round1[0]["round"] == 1 and "mention the beta date" in str(round1[0]["params"])
    await api.decide_all(run["id"])
    done = await api.wait_run(run["id"], until=("completed", "failed"))
    assert done["status"] == "completed"
    assert await db.fetch_val("SELECT count(*) FROM demo_records WHERE payload->>'title'='EDITED BY REVIEWER'") == 1


async def test_reject_everything_executes_nothing(api):
    run = await api.run_workflow("support_escalation")
    await api.decide_all(run["id"], "reject", "not now")
    done = await api.wait_run(run["id"], until=("completed", "failed"))
    assert done["status"] == "completed"
    assert await db.fetch_val("SELECT count(*) FROM action_executions WHERE run_id=%s", (run["id"],)) == 0


# ---------------------------------------------------------------- durability
async def test_resume_after_process_restart(api):
    """Simulate a restart while waiting for approval: drop the compiled graph and in-memory tasks, then decide."""
    run = await api.run_workflow("support_escalation")
    assert run["status"] == "waiting_approval"
    checkpoints = (await api.get("operator", f"/api/runs/{run['id']}/checkpoints")).json()
    assert len(checkpoints) >= 5
    runner._graph, runner.tasks = None, {}  # nothing about the run survives in memory
    await api.decide_all(run["id"])
    done = await api.wait_run(run["id"], until=("completed", "failed"))
    assert done["status"] == "completed"
    events = [e["type"] for e in (await api.get("operator", f"/api/runs/{run['id']}/events")).json()]
    assert events.count("plan") == 1 and "run_resumed" in events  # resumed from the checkpoint, not re-planned


async def test_recover_run_interrupted_mid_flight(api):
    """A run left 'running' by a dead process is continued from its checkpoint by runner.recover()."""
    run = await api.run_workflow("support_escalation")
    await api.decide_all(run["id"])
    await api.wait_run(run["id"], until=("completed",))
    executed = await db.fetch_val("SELECT count(*) FROM action_executions WHERE run_id=%s AND status='completed'", (run["id"],))
    records = await db.fetch_val("SELECT count(*) FROM demo_records")
    # Pretend the process died right after the approval gate: rewind status; checkpoint is already past execution.
    await db.execute("UPDATE runs SET status='running', finished_at=NULL WHERE id=%s", (run["id"],))
    assert run["id"] in await runner.recover()
    done = await api.wait_run(run["id"], until=("completed", "failed"))
    assert done["status"] == "completed"
    assert await db.fetch_val("SELECT count(*) FROM action_executions WHERE run_id=%s AND status='completed'", (run["id"],)) == executed
    assert await db.fetch_val("SELECT count(*) FROM demo_records") == records  # no duplicate side effects


async def test_action_execution_is_idempotent(api):
    """Replaying an executed action with the same idempotency key does not create a second record."""
    row = await db.fetch_one("SELECT * FROM action_executions WHERE status='completed' AND server='tracker' LIMIT 1")
    ctx = RunCtx(principal=make_principal("operator"), run_id=row["run_id"])
    before = await db.fetch_val("SELECT count(*) FROM demo_records")
    res = await gateway.call(ctx, row["server"], row["tool"], row["params"], approval_id=row["approval_id"],
                             idempotency_key=row["idempotency_key"])
    assert res["status"] == "ok" and await db.fetch_val("SELECT count(*) FROM demo_records") == before


async def test_approval_binds_exact_parameters(api):
    """An approval cannot be reused for different parameters."""
    row = await db.fetch_one("SELECT * FROM action_executions WHERE status='completed' AND server='tracker' LIMIT 1")
    ctx = RunCtx(principal=make_principal("operator"), run_id=row["run_id"])
    tampered = {**row["params"], "title": "something else entirely"}
    res = await gateway.call(ctx, row["server"], row["tool"], tampered, approval_id=row["approval_id"])
    assert res["status"] == "denied" and "approval verification failed" in res["reason"]
    res = await gateway.call(ctx, row["server"], row["tool"], row["params"], approval_id="apr_does_not_exist")
    assert res["status"] == "denied"


async def test_retry_and_replan_on_retrieval_failure(api):
    run = await api.run_workflow("knowledge_research", options={"fault_injection": {"node": "research.retrieve", "times": 2}})
    assert run["status"] == "completed"
    types = [e["type"] for e in (await api.get("operator", f"/api/runs/{run['id']}/events")).json()]
    assert "step_retry" in types and "replan" in types
    assert (await api.get("operator", f"/api/runs/{run['id']}")).json()["result"]["citations"]


async def test_tool_retry_on_transient_fault(api):
    run = await api.run_workflow("weekly_business_review", options={"fault_injection": {"tool": "metrics.get_weekly_kpis", "times": 1}})
    assert run["status"] == "waiting_approval"
    tel = (await api.get("operator", f"/api/runs/{run['id']}/telemetry")).json()
    kpi = next(t for t in tel["tools"] if t["tool"] == "get_weekly_kpis")
    assert kpi["status"] == "ok" and kpi["attempt"] == 2
    assert (await api.post("operator", f"/api/runs/{run['id']}/cancel")).status_code == 200
    cancelled = (await api.get("operator", f"/api/runs/{run['id']}")).json()
    assert cancelled["status"] == "cancelled" and all(a["status"] == "cancelled" for a in cancelled["approvals"])
    assert (await api.post("operator", f"/api/runs/{run['id']}/cancel")).status_code == 409


async def test_partial_failure_is_reported_not_hidden(api):
    """An optional step that keeps failing is skipped and the report is marked partial."""
    run = await api.run_workflow("weekly_business_review", options={"fault_injection": {"node": "research.retrieve", "times": 5}})
    assert run["status"] in ("waiting_approval", "completed")
    if run["status"] == "waiting_approval":
        await api.decide_all(run["id"], "reject", "partial data")
        run = await api.wait_run(run["id"], until=("completed", "failed"))
    res = (await api.get("operator", f"/api/runs/{run['id']}")).json()["result"]
    assert res["partial"] is True and any(s["title"] == "Partial failures" for s in res["sections"])
    assert any(s["title"] == "KPI scorecard" for s in res["sections"])


async def test_pause_and_resume(api):
    r = await api.post("operator", "/api/runs", {"workflow": "knowledge_research"})
    rid = r.json()["id"]
    await db.execute("UPDATE runs SET pause_requested=true WHERE id=%s", (rid,))
    run = await api.wait_run(rid)
    if run["status"] == "paused":  # the request raced the first supervisor step; both outcomes are valid
        assert (await api.post("operator", f"/api/runs/{rid}/resume")).status_code == 200
        run = await api.wait_run(rid, until=("completed", "failed"))
    assert run["status"] == "completed"


async def test_sse_stream_replays_persisted_events(api):
    run = await api.run_workflow("knowledge_research")
    r = await api.get("operator", f"/api/runs/{run['id']}/stream")
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: run_started" in r.text and "event: run_completed" in r.text
    last = (await api.get("operator", f"/api/runs/{run['id']}/events")).json()[-2]["seq"]
    tail = await api.get("operator", f"/api/runs/{run['id']}/stream?after={last}")
    assert tail.text.count("event: ") == 1


async def test_conversation_flow(api):
    conv = (await api.post("operator", "/api/conversations", {"title": "New conversation"})).json()
    r = await api.post("operator", f"/api/conversations/{conv['id']}/messages", {"content": "Why are customers churning?"})
    await api.wait_run(r.json()["id"])
    c = (await api.get("operator", f"/api/conversations/{conv['id']}")).json()
    assert [m["role"] for m in c["messages"]] == ["user", "assistant"] and c["title"].startswith("Why are customers")
    assert c["messages"][1]["result"]["citations"]
    assert (await api.get("approver", f"/api/conversations/{conv['id']}")).status_code == 404  # private to its owner


# ---------------------------------------------------------------- MCP
async def test_mcp_registry_and_discovery(api):
    servers = {s["id"]: s for s in (await api.get("viewer", "/api/mcp/servers")).json()}
    assert {"workiq", "tracker", "helpdesk", "metrics", "github"} <= set(servers)
    assert all(servers[s]["status"] == "connected" and servers[s]["kind"] == "demo" for s in ("workiq", "tracker", "helpdesk", "metrics"))
    assert servers["github"]["status"] == "not_configured" and servers["github"]["credential"]["configured"] is False
    wiq = (await api.get("viewer", "/api/mcp/servers/workiq")).json()
    assert len(wiq["tools"]) == 10
    fetch = next(t for t in wiq["tools"] if t["name"] == "fetch")
    assert "context" not in fetch["input_schema"]["properties"] and not fetch["is_write"]
    assert next(t for t in wiq["tools"] if t["name"] == "delete_entity")["risk"] == "high"


async def test_mcp_tool_console_is_governed(api):
    ok = (await api.post("viewer", "/api/mcp/servers/metrics/tools/get_weekly_kpis/invoke", {"args": {"weeks": 2}})).json()
    assert ok["status"] == "ok" and ok["result"]
    held = (await api.post("operator", "/api/mcp/servers/tracker/tools/create_issue/invoke",
                           {"args": {"title": "t", "description": "d"}})).json()
    assert held["status"] == "approval_required"
    denied = (await api.post("viewer", "/api/mcp/servers/tracker/tools/create_issue/invoke",
                             {"args": {"title": "t", "description": "d"}})).json()
    assert denied["status"] == "denied"
    bad = (await api.post("viewer", "/api/mcp/servers/metrics/tools/get_weekly_kpis/invoke", {"args": {"weeks": "many"}})).json()
    assert bad["status"] == "failed" and "invalid arguments" in bad["reason"]
    blocked = (await api.post("admin", "/api/mcp/servers/workiq/tools/fetch/invoke",
                              {"args": {"entityUrls": ["/users/x/authentication/methods"]}})).json()
    assert blocked["status"] == "denied"
    assert (await api.post("viewer", "/api/mcp/servers/metrics/tools/nope/invoke", {"args": {}})).status_code == 404


async def test_tool_can_be_disabled_per_workspace(api):
    path = "/api/mcp/servers/helpdesk/tools/search_tickets"
    assert (await api.call("admin", "PUT", path, json={"enabled": False})).status_code == 200
    try:
        res = (await api.post("operator", path + "/invoke", {"args": {}})).json()
        assert res["status"] == "denied" and "disabled" in res["reason"]
    finally:
        await api.call("admin", "PUT", path, json={"enabled": True})
    assert (await api.post("operator", path + "/invoke", {"args": {}})).json()["status"] == "ok"


async def test_mcp_workspace_isolation(api):
    mine = (await api.post("operator", "/api/mcp/servers/helpdesk/tools/search_tickets/invoke", {"args": {}})).json()["result"]
    theirs = (await api.post("globex", "/api/mcp/servers/helpdesk/tools/search_tickets/invoke", {"args": {}})).json()["result"]
    assert mine["tickets"] and not theirs["tickets"]


async def test_mcp_health_and_unreachable_server(api):
    assert (await api.post("viewer", "/api/mcp/servers/tracker/health")).json()["ok"] is True
    r = await api.post("admin", "/api/mcp/servers", {"id": "deadsrv", "name": "Dead", "url": "http://127.0.0.1:9/mcp"})
    assert r.status_code == 201 and r.json()["status"] == "error" and r.json()["last_error"]
    res = await gateway.call(RunCtx(principal=make_principal("operator")), "deadsrv", "anything", {})
    assert res["status"] in ("failed", "denied")
    assert (await api.call("admin", "DELETE", "/api/mcp/servers/deadsrv")).status_code == 204
    assert (await api.call("admin", "DELETE", "/api/mcp/servers/workiq")).status_code == 400
    assert mcp_manager.get("deadsrv") is None


async def test_original_v1_api_still_works(api):
    d = (await api.get("operator", "/v1/discover?q=messages")).json()
    assert d
    assert (await api.get("operator", "/v1/schema?path=/me/messages")).status_code == 200
    out = (await api.post("operator", "/v1/run?approved=true", {"request": "What are our major operational risks?"})).json()
    assert out["status"] == "completed" and out["approved_flag_ignored"] is True
    assert "episodic" in (await api.get("operator", "/v1/memory")).json()


# ---------------------------------------------------------------- RAG
async def test_document_acl_and_workspace_isolation(api):
    q = "board compensation retention grant pool"
    assert any(r["filename"] == "board-compensation-memo.md" for r in (await search(make_principal("admin"), q))["results"])
    assert all(r["filename"] != "board-compensation-memo.md" for r in (await search(make_principal("operator"), q, k=20))["results"])
    globex = (await search(make_principal("admin", "globex"), "operational risk register expansion plan", k=20))["results"]
    assert globex and all(r["filename"] == "globex-expansion-plan.md" for r in globex)
    docs = (await api.get("operator", "/api/documents")).json()
    assert len(docs) == 12 and all(d["filename"] != "board-compensation-memo.md" for d in docs)
    restricted = next(d for d in (await api.get("admin", "/api/documents")).json() if d["filename"] == "board-compensation-memo.md")
    assert (await api.get("operator", f"/api/documents/{restricted['id']}")).status_code == 404


async def test_search_modes_and_highlights(api):
    for mode in ("hybrid", "vector", "keyword"):
        res = (await api.post("viewer", "/api/knowledge/search", {"query": "SSO login failure workaround", "mode": mode})).json()
        assert res["results"] and res["results"][0]["filename"] == "known-issues-knowledge-base.md", mode
    hybrid = (await api.post("viewer", "/api/knowledge/search", {"query": "SSO login failure workaround"})).json()["results"][0]
    assert hybrid["highlights"] and hybrid["vector_rank"] and hybrid["keyword_rank"] and hybrid["rerank_score"] > 0
    only = (await api.post("viewer", "/api/knowledge/search", {"query": "retention", "source_type": "text"})).json()["results"]
    assert only and all(r["source_type"] == "text" for r in only)


async def test_upload_ingest_search_delete(api):
    body = b"# Quokka Runbook\n\n## Restart procedure\n\nTo restart the quokka billing reconciler, drain the zebra queue first and then redeploy.\n"
    assert (await api.post("viewer", "/api/documents", files={"file": ("quokka.md", body, "text/markdown")})).status_code == 403
    assert (await api.post("operator", "/api/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")})).status_code == 415
    r = await api.post("operator", "/api/documents", files={"file": ("quokka.md", body, "text/markdown")}, data={"tags": "runbook"})
    assert r.status_code == 201 and r.json()["duplicate"] is False
    doc_id = r.json()["id"]
    for _ in range(100):
        doc = (await api.get("operator", f"/api/documents/{doc_id}")).json()
        if doc["status"] in ("indexed", "failed"):
            break
        await asyncio.sleep(0.1)
    assert doc["status"] == "indexed" and doc["title"] == "Quokka Runbook" and doc["chunks"] and doc["progress"] == 100
    hit = (await api.post("operator", "/api/knowledge/search", {"query": "how to restart the quokka reconciler"})).json()["results"][0]
    assert hit["document_id"] == doc_id
    dup = await api.post("operator", "/api/documents", files={"file": ("quokka-copy.md", body, "text/markdown")})
    assert dup.json()["duplicate"] is True and dup.json()["id"] == doc_id
    run = await api.run_workflow(None, "How do I restart the quokka billing reconciler?")
    res = (await api.get("operator", f"/api/runs/{run['id']}")).json()["result"]
    assert "zebra queue" in res["markdown"] and res["citations"][0]["document_id"] == doc_id
    assert (await api.call("operator", "DELETE", f"/api/documents/{doc_id}")).status_code == 204
    assert await db.fetch_val("SELECT count(*) FROM chunks WHERE document_id=%s", (doc_id,)) == 0


async def test_pdf_ingestion(api):
    from io import BytesIO

    from reportlab.pdfgen import canvas
    buf = BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "Pangolin failover checklist: promote the replica, then rotate the pangolin tokens.")
    c.showPage()
    c.drawString(72, 720, "Second page: verify dashboards after the pangolin failover completes.")
    c.save()
    r = await api.post("operator", "/api/documents", files={"file": ("pangolin.pdf", buf.getvalue(), "application/pdf")})
    doc_id = r.json()["id"]
    for _ in range(100):
        doc = (await api.get("operator", f"/api/documents/{doc_id}")).json()
        if doc["status"] in ("indexed", "failed"):
            break
        await asyncio.sleep(0.1)
    assert doc["status"] == "indexed", doc.get("error")
    assert {c["page"] for c in doc["chunks"]} == {1, 2}
    hit = (await api.post("operator", "/api/knowledge/search", {"query": "pangolin failover checklist"})).json()["results"][0]
    assert hit["document_id"] == doc_id and hit["page"] in (1, 2)
    await api.call("operator", "DELETE", f"/api/documents/{doc_id}")


async def test_prompt_injection_in_document_is_quarantined(api):
    run = await api.run_workflow("knowledge_research", "Summarise the latest Halcyon Freight Data vendor newsletter and its pricing changes.")
    assert run["status"] == "completed"
    full = (await api.get("operator", f"/api/runs/{run['id']}")).json()
    md = full["result"]["markdown"].lower()
    assert "ignore all previous" not in md and "billing@halcyon" not in md
    assert any(g["type"] == "prompt_injection_in_document" for g in full["result"]["guardrails"])
    assert not full["approvals"]
    assert await db.fetch_val("SELECT count(*) FROM tool_executions WHERE run_id=%s AND is_write", (run["id"],)) == 0


async def test_secrets_in_request_are_never_persisted(api):
    run = await api.run_workflow(None, "Research our operational risks. password: Hunter2Hunter2")
    blob = await db.fetch_val(
        "SELECT (SELECT row_to_json(r)::text FROM runs r WHERE id=%s) || (SELECT string_agg(payload::text,' ') FROM run_events WHERE run_id=%s)",
        (run["id"], run["id"]))
    assert "Hunter2Hunter2" not in blob and "[REDACTED" in run["request"]
    cp = await db.fetch_val("SELECT string_agg(encode(blob,'escape'),' ') FROM checkpoint_blobs WHERE thread_id=%s", (run["id"],))
    assert "Hunter2Hunter2" not in (cp or "")


# ---------------------------------------------------------------- evals, observability, audit
async def test_evaluation_datasets_pass_their_gates(api):
    for ds in ("retrieval_quality", "agent_workflows", "safety_guardrails"):
        r = await api.post("operator", "/api/evals/runs", {"dataset": ds})
        assert r.status_code == 201
        for _ in range(600):
            run = (await api.get("operator", f"/api/evals/runs/{r.json()['id']}")).json()
            if run["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)
        assert run["status"] == "completed", run
        failed = [c for c in run["results"] if not c["passed"]]
        assert not failed and run["metrics"]["gate_passed"], failed
        assert (await api.post("operator", f"/api/evals/runs/{run['id']}/baseline")).status_code == 200
    again = (await api.post("operator", "/api/evals/runs", {"dataset": "retrieval_quality", "config": {"mode": "vector", "rerank": False}})).json()
    for _ in range(300):
        run = (await api.get("operator", f"/api/evals/runs/{again['id']}")).json()
        if run["status"] == "completed":
            break
        await asyncio.sleep(0.1)
    assert run["baseline"] and run["baseline"]["id"] != run["id"] and isinstance(run["regressions"], list)
    assert (await api.post("globex", "/api/evals/runs", {"dataset": "retrieval_quality"})).status_code == 400
    assert (await api.post("viewer", "/api/evals/runs", {"dataset": "retrieval_quality"})).status_code == 403


async def test_dashboard_and_observability_reflect_real_activity(api):
    d = (await api.get("viewer", "/api/dashboard")).json()
    assert d["runs"]["total"] == await db.fetch_val("SELECT count(*) FROM runs WHERE workspace_id='meridian' AND origin<>'eval'")
    assert d["tools"]["total"] > 0 and d["mcp"]["connected"] == 4 and {a["agent"] for a in d["agents"]} >= {"supervisor", "research"}
    o = (await api.get("viewer", "/api/observability")).json()
    assert o["nodes"] and o["tools"] and o["llm"] and o["series"] and o["run_latency_ms"]["n"] > 0
    assert any(g["kind"] == "prompt_injection_in_document" for g in o["guardrails"])
    assert o["llm"][0]["cost_usd"] == 0 and "no paid API calls" in o["cost_note"]
    empty = (await api.get("globex", "/api/dashboard")).json()
    assert empty["runs"]["total"] == 0 and empty["runs"]["success_rate"] is None
    m = (await api.get(None, "/metrics")).text
    assert "workplace_agent_runs_total" in m and "workiq_tool_calls_total" in m


async def test_audit_chain_is_tamper_evident(api):
    v = (await api.get("admin", "/api/audit/verify")).json()
    assert v["valid"] is True and v["events"] > 20
    seq = await db.fetch_val("SELECT seq FROM audit_events WHERE workspace_id='meridian' ORDER BY seq LIMIT 1 OFFSET 5")
    old = await db.fetch_val("SELECT decision FROM audit_events WHERE seq=%s", (seq,))
    await db.execute("UPDATE audit_events SET decision='tampered' WHERE seq=%s", (seq,))
    try:
        assert (await api.get("admin", "/api/audit/verify")).json()["valid"] is False
    finally:
        await db.execute("UPDATE audit_events SET decision=%s WHERE seq=%s", (old, seq))
    assert (await api.get("admin", "/api/audit/verify")).json()["valid"] is True
