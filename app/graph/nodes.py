"""Top-level graph nodes: intake guardrails, supervisor, compliance, human approval gate, action execution,
synthesizer, evaluator and finalize."""
from __future__ import annotations

import hashlib

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app import db
from app.audit import audit
from app.config import settings
from app.context import RunCtx
from app.graph import planner
from app.graph.runtime import node
from app.graph.state import AgentState
from app.graph.tasks import fmt
from app.llm.model import compose, estimate_tokens, model_info, record
from app.mcp.gateway import call_tool, gateway, params_hash
from app.memory import memory
from app.rag.ingest import scan_untrusted
from app.rag.metrics import support
from app.redaction import find_sensitive, redact_text

MAX_STEP_ATTEMPTS = 2
MAX_APPROVAL_ROUNDS = 2
SUPPORT_THRESHOLD = 0.6


# ---------------------------------------------------------------- intake
@node("intake", "supervisor")
async def intake(state: AgentState, ctx: RunCtx) -> dict:
    request, guardrails = state["request"], []
    for g in state.get("guardrails") or []:  # raised by the runner, which redacts before anything is persisted
        ctx.emit({**g, "type": "guardrail", "kind": g["type"]})
    leaks = find_sensitive(request)
    if leaks:
        request = redact_text(request)
        guardrails.append({"type": "sensitive_data_in_request", "action": "redacted before processing", "flags": leaks})
    flags = scan_untrusted(request)
    if flags:
        guardrails.append({"type": "prompt_injection_in_request", "flags": flags,
                           "action": "flagged; tool permissions and approvals are enforced server-side regardless of prompt text"})
    for g in guardrails:
        ctx.emit({**g, "type": "guardrail", "kind": g["type"]})
    return {"request": request, "guardrails": guardrails, "replans": 0, "revisions": 0, "approval_round": 0}


# ---------------------------------------------------------------- supervisor
@node("supervisor", "supervisor")
async def supervisor(state: AgentState, ctx: RunCtx) -> dict:
    if ctx.run_id and await db.fetch_val("SELECT pause_requested FROM runs WHERE id=%s", (ctx.run_id,)):
        ctx.emit({"type": "paused", "reason": "pause requested by user"})
        interrupt({"type": "pause"})
    plan = [dict(s) for s in state.get("plan") or []]
    out: dict = {}
    if not plan:
        intent, plan = planner.build_plan(state["request"], state.get("workflow"), ctx.options)
        await record(ctx, "planning", "deterministic", "deterministic-demo-v1", estimate_tokens(state["request"]),
                     sum(estimate_tokens(s["title"]) for s in plan), 0, True)
        ctx.emit({"type": "plan", "intent": intent, "steps": [{k: s[k] for k in ("id", "agent", "title", "mode")} for s in plan]})
        out["intent"] = intent
    else:
        res = state.get("step_result") or {}
        cur = next((s for s in plan if s["id"] == state.get("cursor")), None)
        if cur and cur["status"] == "running":
            if res.get("status") == "failed":
                cur["attempts"] += 1
                cur["error"] = res.get("error")
                if cur["attempts"] < MAX_STEP_ATTEMPTS:
                    cur["status"] = "pending"
                    ctx.emit({"type": "step_retry", "step": cur["id"], "agent": cur["agent"], "attempt": cur["attempts"] + 1,
                              "error": cur["error"]})
                    if ctx.run_id:
                        await db.execute("UPDATE runs SET retry_count=retry_count+1 WHERE id=%s", (ctx.run_id,))
                else:
                    cur["status"] = "failed"
                    ctx.emit({"type": "step_end", "step": cur["id"], "agent": cur["agent"], "status": "failed", "error": cur["error"]})
                    replans = state.get("replans", 0)
                    if cur["agent"] == "research" and cur["params"].get("retrieval_mode") != "keyword" and replans < 1:
                        alt = {**cur, "id": cur["id"] + "b", "title": cur["title"] + " (keyword-only fallback)", "status": "pending",
                               "attempts": 0, "error": None, "params": {**cur["params"], "retrieval_mode": "keyword"}}
                        plan.insert(plan.index(cur) + 1, alt)
                        out["replans"] = replans + 1
                        ctx.emit({"type": "replan", "reason": f"step {cur['id']} failed after {cur['attempts']} attempts",
                                  "change": f"added {alt['id']}: {alt['title']}"})
                    elif not cur.get("optional"):
                        for s in plan:
                            if s["status"] == "pending":
                                s["status"] = "skipped"
                        ctx.emit({"type": "replan", "reason": f"required step {cur['id']} failed", "change": "remaining steps skipped; reporting partial results"})
            else:
                cur["status"], cur["summary"], cur["error"] = "completed", res.get("summary"), None
                ctx.emit({"type": "step_end", "step": cur["id"], "agent": cur["agent"], "status": "completed", "summary": cur["summary"]})
    nxt = next((s for s in plan if s["status"] == "pending"), None)
    if nxt:
        nxt["status"] = "running"
        ctx.emit({"type": "step_start", "step": nxt["id"], "agent": nxt["agent"], "title": nxt["title"], "attempt": nxt["attempts"] + 1})
        out.update(cursor=nxt["id"], route=nxt["agent"])
    else:
        out.update(cursor=None, route="compliance")
    out.update(plan=plan, step_result=None, scratch={})
    if ctx.run_id:
        await db.execute("UPDATE runs SET plan=%s, intent=COALESCE(%s, intent), updated_at=now() WHERE id=%s",
                         (db.J(plan), out.get("intent"), ctx.run_id))
    return out


# ---------------------------------------------------------------- compliance
@node("compliance", "compliance")
async def compliance(state: AgentState, ctx: RunCtx) -> dict:
    """Policy review of every proposed action. Uses the same server-side policy engine as the MCP gateway."""
    ev = {e["id"]: e for e in state.get("evidence") or []}
    reviewed = []
    for a in state.get("proposed_actions") or []:
        if a["status"] not in ("proposed", "revised"):
            continue
        d = await gateway.evaluate(ctx, a["server"], a["tool"], a["params"], approved=False)
        a = {**a, "risk": d.risk, "policy_reason": d.reason}
        if any(ev.get(c, {}).get("untrusted") for c in a.get("evidence", [])):
            a["risk"] = "high"
            a["policy_reason"] += "; cites a document flagged as untrusted"
        a["status"] = {"deny": "blocked", "approval_required": "needs_approval", "allow": "approved"}[d.effect]
        reviewed.append(a)
        ctx.emit({"type": "compliance_review", "action_id": a["id"], "title": a["title"], "tool": f"{a['server']}.{a['tool']}",
                  "decision": d.effect, "risk": a["risk"], "reason": a["policy_reason"]})
    return {"proposed_actions": reviewed}


def after_compliance(state: AgentState, config: RunnableConfig) -> str:
    acts = state.get("proposed_actions") or []
    if (config.get("configurable", {}).get("options") or {}).get("propose_only"):
        return "synthesizer"  # evaluation runs stop at the proposal: nothing is approved or executed
    if any(a["status"] == "needs_approval" for a in acts):
        return "approval_gate"
    return "execute_actions" if any(a["status"] == "approved" for a in acts) else "synthesizer"


# ---------------------------------------------------------------- human approval gate
def approval_id(run_id: str, action_id: str, rnd: int) -> str:
    return "apr_" + hashlib.sha256(f"{run_id}|{action_id}|{rnd}".encode()).hexdigest()[:14]


@node("approval_gate", "compliance")
async def approval_gate(state: AgentState, ctx: RunCtx) -> dict:
    """Persist approval requests, then interrupt(). The node re-runs on resume, so every write here is idempotent
    and decisions are always read from the database (never from the resume payload)."""
    rnd = state.get("approval_round", 0)
    ev = {e["id"]: e for e in state.get("evidence") or []}
    pending = [a for a in state.get("proposed_actions") or [] if a["status"] == "needs_approval"]
    created = 0
    for a in pending:
        evidence = [{k: ev[c].get(k) for k in ("id", "title", "heading", "page", "snippet", "document_id", "untrusted")}
                    for c in a.get("evidence", []) if c in ev]
        created += await db.execute(
            """INSERT INTO approvals(id, run_id, workspace_id, action_id, round, risk, server, tool, title, rationale, params,
               params_hash, evidence, requesting_agent, policy_reason) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (run_id, action_id, round) DO NOTHING""",
            (approval_id(ctx.run_id, a["id"], rnd), ctx.run_id, ctx.workspace_id, a["id"], rnd, a["risk"], a["server"], a["tool"],
             a["title"], a["rationale"], db.J(a["params"]), params_hash(a["params"]), db.J(evidence), a["agent"],
             a.get("policy_reason")))
    ids = [approval_id(ctx.run_id, a["id"], rnd) for a in pending]
    rows = await db.fetch_all("SELECT * FROM approvals WHERE id = ANY(%s)", (ids,))
    if any(r["status"] == "pending" for r in rows):
        if created:
            ctx.emit({"type": "approval_requested", "round": rnd, "approvals": [
                {"id": r["id"], "title": r["title"], "risk": r["risk"], "tool": f"{r['server']}.{r['tool']}"} for r in rows]})
            await audit.append({"workspace_id": ctx.workspace_id, "actor": "system", "action": "approval.requested",
                                "resource": ctx.run_id, "decision": "pending", "run_id": ctx.run_id, "detail": {"approvals": ids}})
        interrupt({"type": "approval", "approval_ids": ids, "round": rnd})
        rows = await db.fetch_all("SELECT * FROM approvals WHERE id = ANY(%s)", (ids,))
    by_action = {r["action_id"]: r for r in rows}
    updated = []
    for a in pending:
        r = by_action[a["id"]]
        st = r["status"]
        if st in ("approved", "edited"):
            a = {**a, "status": "approved", "approval_id": r["id"], "params": r["edited_params"] if st == "edited" else a["params"],
                 "edited": st == "edited"}
        elif st == "changes_requested":
            a = {**a, "status": "changes_requested", "review_comment": r["comment"] or ""}
        else:
            a = {**a, "status": "rejected", "review_comment": r["comment"] or ("no decision recorded" if st == "pending" else "")}
        a["decided_by"] = r["decided_by_name"]
        updated.append(a)
        ctx.emit({"type": "approval_decision", "approval_id": r["id"], "action_id": a["id"], "title": a["title"],
                  "decision": st, "by": r["decided_by_name"], "comment": r["comment"]})
    return {"proposed_actions": updated}


def after_approval(state: AgentState) -> str:
    acts = state.get("proposed_actions") or []
    if any(a["status"] == "changes_requested" for a in acts):
        return "revise_actions"
    return "execute_actions" if any(a["status"] == "approved" for a in acts) else "synthesizer"


def apply_review(params: dict, comment: str) -> dict:
    """Deterministic revision: attach the reviewer's instruction to the human-readable field of the action."""
    note = f"\n\n[Revised per reviewer: {comment}]"
    p = {**params}
    if "description" in p:
        p["description"] = p["description"] + note
    elif "reason" in p:
        p["reason"] = p["reason"] + note
    elif isinstance(p.get("body"), dict) and "message" in p["body"]:
        msg = {**p["body"]["message"]}
        body = {**(msg.get("body") or {})}
        body["content"] = (body.get("content") or "") + note
        msg["body"] = body
        p["body"] = {**p["body"], "message": msg}
    return p


@node("revise_actions", "communication")
async def revise_actions(state: AgentState, ctx: RunCtx) -> dict:
    rnd = state.get("approval_round", 0) + 1
    out = []
    for a in state.get("proposed_actions") or []:
        if a["status"] != "changes_requested":
            continue
        if rnd > MAX_APPROVAL_ROUNDS:
            out.append({**a, "status": "rejected", "review_comment": "revision limit reached"})
            continue
        params = apply_review(a["params"], a.get("review_comment", ""))
        text = await compose(ctx, "revise_action", str(a["params"]) + "\nReviewer: " + a.get("review_comment", ""), str(params))
        del text
        out.append({**a, "params": params, "status": "revised", "round": rnd})
        ctx.emit({"type": "action_revised", "action_id": a["id"], "title": a["title"], "round": rnd, "comment": a.get("review_comment")})
    return {"proposed_actions": out, "approval_round": rnd}


# ---------------------------------------------------------------- execute
@node("execute_actions", "task")
async def execute_actions(state: AgentState, ctx: RunCtx) -> dict:
    """Execute approved actions exactly once. The idempotency key covers run, action and exact parameters."""
    results, updated = [], []
    for a in state.get("proposed_actions") or []:
        if a["status"] != "approved":
            continue
        key = hashlib.sha256(f"{ctx.run_id}|{a['id']}|{params_hash(a['params'])}".encode()).hexdigest()[:32]
        await db.execute(
            """INSERT INTO action_executions(idempotency_key, run_id, workspace_id, action_id, approval_id, server, tool, params)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (idempotency_key) DO UPDATE SET attempts = action_executions.attempts + 1""",
            (key, ctx.run_id, ctx.workspace_id, a["id"], a.get("approval_id"), a["server"], a["tool"], db.J(a["params"])))
        prior = await db.fetch_one("SELECT status, result FROM action_executions WHERE idempotency_key=%s", (key,))
        if prior["status"] == "completed":
            res = {"id": a["id"], "status": "executed", "result": prior["result"], "replayed": True}
            ctx.emit({"type": "action_replayed", "action_id": a["id"], "title": a["title"], "reason": "already executed; idempotent replay"})
        else:
            r = await call_tool(ctx, a["server"], a["tool"], a["params"], approval_id=a.get("approval_id"), idempotency_key=key)
            ok = r["status"] == "ok"
            await db.execute("UPDATE action_executions SET status=%s, result=%s, error=%s, finished_at=now() WHERE idempotency_key=%s",
                             ("completed" if ok else "failed", db.J(r.get("result")) if ok else None, r.get("reason"), key))
            res = {"id": a["id"], "status": "executed" if ok else "failed", "result": r.get("result"), "error": r.get("reason"),
                   "replayed": False}
        res.update(title=a["title"], tool=f"{a['server']}.{a['tool']}", approval_id=a.get("approval_id"),
                   dry_run=ctx.dry_run, idempotency_key=key)
        results.append(res)
        updated.append({**a, "status": res["status"]})
        ctx.emit({"type": "action_executed", "action_id": a["id"], "title": a["title"], "status": res["status"],
                  "dry_run": ctx.dry_run, "error": res.get("error"), "result": res.get("result")})
    return {"action_results": results, "proposed_actions": updated}


# ---------------------------------------------------------------- synthesizer
class Cites:
    def __init__(self, evidence: list[dict]):
        self.ev = {e["id"]: e for e in evidence}
        self.order: list[str] = []

    def mark(self, ids: list[str]) -> str:
        out = []
        for i in ids:
            if i not in self.ev:
                continue
            if i not in self.order:
                self.order.append(i)
            out.append(f"[{self.order.index(i) + 1}]")
        return "".join(out)

    def listing(self) -> list[dict]:
        return [{"n": n + 1, **{k: self.ev[i].get(k) for k in ("id", "chunk_id", "document_id", "title", "filename", "heading",
                                                               "page", "snippet", "score", "untrusted", "highlights")}}
                for n, i in enumerate(self.order)]


def _claims(findings: list[dict], ref: str | None, cites: Cites, drop: set[str], limit: int = 6, exclude_refs: tuple = ()) -> list[dict]:
    out, seen = [], set()
    for f in sorted(findings, key=lambda f: -f.get("score", 0)):
        if (ref is not None and f.get("ref") != ref) or f.get("ref") in exclude_refs or f["id"] in drop:
            continue
        if any(f["text"] in t or t in f["text"] for t in seen):
            continue
        seen.add(f["text"])
        out.append({"text": f["text"], "citations": f["citations"], "marks": cites.mark(f["citations"]), "finding_id": f["id"],
                    "topic": f.get("topic")})
        if len(out) >= limit:
            break
    return out


def _action_items(state: AgentState) -> list[dict]:
    results = {r["id"]: r for r in state.get("action_results") or []}
    items = []
    for a in state.get("proposed_actions") or []:
        r = results.get(a["id"], {})
        ref = (r.get("result") or {}).get("id") or (r.get("result") or {}).get("messageId") if isinstance(r.get("result"), dict) else None
        label = {"executed": "Executed", "failed": "Failed", "rejected": "Rejected by reviewer", "blocked": "Blocked by policy",
                 "needs_approval": "Awaiting approval", "approved": "Approved"}.get(a["status"], a["status"].replace("_", " ").title())
        if a["status"] == "executed" and r.get("dry_run"):
            label = "Dry run (no change made)"
        items.append({"text": a["title"], "status": a["status"], "status_label": label, "tool": f"{a['server']}.{a['tool']}",
                      "risk": a.get("risk"), "reference": ref, "decided_by": a.get("decided_by"), "edited": a.get("edited", False),
                      "detail": a.get("policy_reason") if a["status"] == "blocked" else r.get("error") or a.get("review_comment") or ""})
    return items


@node("synthesizer", "synthesizer")
async def synthesizer(state: AgentState, ctx: RunCtx) -> dict:
    intent = state.get("intent") or "knowledge_research"
    findings, an, ex = state.get("findings") or [], state.get("analytics") or {}, state.get("extracted") or {}
    drop = set((state.get("evaluation") or {}).get("unsupported_ids", [])) if state.get("revisions") else set()
    cites = Cites(state.get("evidence") or [])
    sections: list[dict] = []
    title, summary = "Answer", ""

    if intent == "weekly_business_review" and an.get("kpis"):
        title = "Weekly Executive Business Review"
        anomalies = an.get("anomalies", [])
        top = anomalies[0] if anomalies else None
        summary = f"{len(anomalies)} of {len(an['kpis'])} tracked KPIs need attention this week."
        if top:
            summary += (f" The largest gap is {top['label']} at {fmt(top['latest'], top['unit'])} against a target of "
                        f"{fmt(top['target'], top['unit'])} ({top['gap_pct']:+.1f}%).")
        sections.append({"title": "KPI scorecard", "kind": "kpis", "items": [
            {k: v for k, v in kp.items() if k != "description"} for kp in an["kpis"]]})
        sections.append({"title": "Anomalies", "kind": "bullets", "items": [
            {"text": f"{a['label']}: {fmt(a['latest'], a['unit'])} vs target {fmt(a['target'], a['unit'])} ({a['gap_pct']:+.1f}%), "
                     f"{a['wow_pct']:+.1f}% week over week, {a['adverse_streak']} adverse week(s) in a row, z-score {a['zscore']}.",
             "severity": a["severity"], "source": "metrics.get_weekly_kpis"} for a in anomalies]})
        causes = []
        used: set[str] = set()
        for a in anomalies[:6]:
            for c in _claims(findings, a["metric"], cites, drop, limit=2):
                if any(c["text"] in u or u in c["text"] for u in used):
                    continue
                used.add(c["text"])
                causes.append({**c, "label": a["label"]})
        if causes:
            sections.append({"title": "Likely causes and business context", "kind": "claims", "items": causes})
        risks = _claims(findings, "risks", cites, drop, limit=3)
        if risks:
            sections.append({"title": "Operational risks", "kind": "claims", "items": risks})
        if an.get("correlations"):
            sections.append({"title": "Correlated movements", "kind": "bullets", "items": [
                {"text": f"{c['a_label']} and {c['b_label']} moved together over the period (r = {c['r']}). Correlation is not causation.",
                 "source": "computed"} for c in an["correlations"][:4]]})
    elif intent == "support_escalation" and an.get("totals"):
        title = "Customer Support Escalation Review"
        t = an["totals"]
        summary = (f"{t['open']} unresolved tickets across {t['customers']} customers; {t['p1']} are priority one and "
                   f"{t['p1_breached']} of those have breached SLA.")
        rec = [c for c in an["clusters"] if c["recurring"]]
        if rec:
            summary += f" The main recurring problem is {rec[0]['label']} ({rec[0]['count']} tickets, {len(rec[0]['customers'])} customers)."
        sections.append({"title": "SLA breaches", "kind": "tickets", "items": an["breaches"]})
        sections.append({"title": "Recurring problems", "kind": "bullets", "items": [
            {"text": f"{c['label']}: {c['count']} open tickets ({c['p1']} P1, {c['breached']} in breach) across "
                     f"{len(c['customers'])} customers with ${c['acv_usd']:,} combined ACV.",
             "severity": "high" if c["recurring"] else "none", "source": "helpdesk.search_tickets"} for c in an["clusters"]]})
        fixes = [c for cl in an["clusters"][:3] for c in _claims(findings, cl["category"], cites, drop, limit=2)]
        if fixes:
            sections.append({"title": "Known fixes and workarounds", "kind": "claims", "items": fixes})
        pol = _claims(findings, "policy", cites, drop, limit=2) + _claims(findings, "comms", cites, drop, limit=1)
        if pol:
            sections.append({"title": "Applicable policy", "kind": "claims", "items": pol})
    elif intent == "meeting_to_execution" and ex.get("meeting"):
        m = ex["meeting"]
        title = f"Meeting to execution: {m.get('subject')}"
        summary = (f"{len(ex['decisions'])} decisions and {len(ex['action_items'])} action items were extracted from the transcript "
                   f"of '{m.get('subject')}'.")
        sections.append({"title": "Decisions", "kind": "bullets", "items": [
            {"text": d["text"], "source": f"transcript · {d['by']}"} for d in ex["decisions"]]})
        sections.append({"title": "Action items", "kind": "action_items", "items": ex["action_items"]})
        if ex.get("open_questions"):
            sections.append({"title": "Open questions", "kind": "bullets", "items": [
                {"text": q["text"], "source": f"transcript · {q['by']}"} for q in ex["open_questions"]]})
        ctxs = _claims(findings, None, cites, drop, limit=3)
        if ctxs:
            sections.append({"title": "Related internal context", "kind": "claims", "items": ctxs})
    elif intent == "workplace_lookup":
        title = "Workplace lookup"
        items = (state.get("workplace") or {}).get("items", [])
        summary = f"Found {len(items)} item(s) through the Work IQ MCP server (simulated Microsoft 365 data)."
        sections.append({"title": "Results", "kind": "bullets", "items": [
            {"text": f"{i['title']}" + (f" — {i['detail']}" if i["detail"] else ""), "source": i["path"]} for i in items]})
    elif intent == "send_email":
        title = "Email request"
        summary = "The email was drafted and routed through policy review and human approval."
    else:
        title = "Knowledge research"
        claims = _claims(findings, None, cites, drop, limit=7)
        if claims:
            docs = len({cites.ev[c]["document_id"] for cl in claims for c in cl["citations"] if c in cites.ev})
            summary = f"Found {len(claims)} supported statement(s) across {docs} document(s)."
            sections.append({"title": "Findings", "kind": "claims", "items": claims})
        else:
            summary = ("No sufficiently relevant evidence was found in the documents you are permitted to access, so no answer "
                       "is given. Try rephrasing or upload the relevant document in Knowledge Studio.")

    actions = _action_items(state)
    if actions:
        sections.append({"title": "Actions", "kind": "actions", "items": actions})
    failures = [{"step": s["id"], "title": s["title"], "error": s.get("error"), "status": s["status"]}
                for s in state.get("plan") or [] if s["status"] in ("failed", "skipped")]
    if failures:
        sections.append({"title": "Partial failures", "kind": "bullets", "items": [
            {"text": f"{f['title']}: {f['status']}" + (f" — {f['error']}" if f["error"] else ""), "severity": "high",
             "source": "runtime"} for f in failures]})
    if state.get("guardrails"):
        sections.append({"title": "Guardrail events", "kind": "bullets", "items": [
            {"text": f"{g['type'].replace('_', ' ').capitalize()}: {g['action']}" + (f" ({g['document']})" if g.get("document") else ""),
             "source": "guardrails"} for g in state["guardrails"]]})

    # Narrative: deterministic composition from the highest-ranked cited claims (or an LLM constrained to them).
    lead = [c for s in sections if s["kind"] == "claims" for c in s["items"]][:3]
    narrative = summary + ("" if not lead else " " + " ".join(f"{c['text']} {c['marks']}" for c in lead))
    context = "\n".join(f"{c['text']} {c['marks']}" for s in sections if s["kind"] == "claims" for c in s["items"]) or summary
    narrative = await compose(ctx, "synthesis", summary + "\n" + context, narrative,
                              "Write a 3-5 sentence executive summary. Keep numbers exact.")
    md = [f"# {title}", "", narrative, ""]
    for s in sections:
        md.append(f"## {s['title']}")
        for it in s["items"]:
            if s["kind"] == "claims":
                md.append(f"- {it['text']} {it['marks']}")
            elif s["kind"] == "kpis":
                md.append(f"- {it['label']}: {fmt(it['latest'], it['unit'])} (target {fmt(it['target'], it['unit'])}, {it['wow_pct']:+.1f}% WoW)"
                          + (" — anomaly" if it["anomaly"] else ""))
            elif s["kind"] == "tickets":
                md.append(f"- {it['id']} · {it['customer']} · {it['priority'].upper()} · open {it['age_hours']}h (SLA {it['sla_hours']}h) — {it['subject']}")
            elif s["kind"] == "action_items":
                md.append(f"- {it['owner']}: {it['task']}" + (f" (due {it['due_date']})" if it["due_date"] else ""))
            elif s["kind"] == "actions":
                md.append(f"- {it['text']} — {it['status_label']}" + (f" ({it['reference']})" if it["reference"] else ""))
            else:
                md.append(f"- {it['text']}")
        md.append("")
    listing = cites.listing()
    if listing:
        md.append("## Sources")
        md += [f"[{c['n']}] {c['title']} — {c['heading'] or 'document'}" + (f", p.{c['page']}" if c["page"] else "") for c in listing]
    draft = {"title": title, "intent": intent, "summary": narrative, "sections": sections, "citations": listing,
             "markdown": "\n".join(md).strip(), "partial": bool(failures), "model": model_info(),
             "simulated_sources": True}
    ctx.emit({"type": "draft", "title": title, "sections": [s["title"] for s in sections], "citations": len(listing),
              "revision": state.get("revisions", 0)})
    return {"draft": draft}


# ---------------------------------------------------------------- evaluator
REQUIRED_SECTIONS = {
    "weekly_business_review": ["KPI scorecard", "Anomalies", "Likely causes and business context"],
    "support_escalation": ["SLA breaches", "Recurring problems", "Known fixes and workarounds"],
    "meeting_to_execution": ["Decisions", "Action items"],
    "knowledge_research": ["Findings"],
}


@node("evaluator", "evaluator")
async def evaluator(state: AgentState, ctx: RunCtx) -> dict:
    draft = state["draft"]
    ev = {e["id"]: e for e in state.get("evidence") or []}
    claims = [c for s in draft["sections"] if s["kind"] == "claims" for c in s["items"]]
    details, unsupported = [], []
    for c in claims:
        best = max((support(c["text"], ev[i]["snippet"]) for i in c["citations"] if i in ev), default=0.0)
        ok = best >= SUPPORT_THRESHOLD
        details.append({"text": c["text"][:160], "support": round(best, 3), "ok": ok})
        if not ok:
            unsupported.append(c["finding_id"])
    groundedness = sum(d["ok"] for d in details) / len(details) if details else 1.0
    required = REQUIRED_SECTIONS.get(draft["intent"], [])
    present = {s["title"] for s in draft["sections"] if s["items"]}
    completeness = sum(r in present for r in required) / len(required) if required else 1.0
    tools = await db.fetch_one(
        "SELECT count(*) AS n, count(*) FILTER (WHERE status IN ('ok','approval_required')) AS ok FROM tool_executions WHERE run_id=%s",
        (ctx.run_id,)) if ctx.run_id else {"n": 0, "ok": 0}
    tool_success = tools["ok"] / tools["n"] if tools["n"] else 1.0
    executed = [r for r in state.get("action_results") or [] if r["status"] == "executed"]
    compliance_ok = all(r.get("approval_id") for r in executed)
    scores = {"groundedness": round(groundedness, 3), "completeness": round(completeness, 3),
              "tool_success": round(tool_success, 3), "policy_compliance": 1.0 if compliance_ok else 0.0}
    overall = round(0.4 * scores["groundedness"] + 0.3 * scores["completeness"] + 0.15 * scores["tool_success"]
                    + 0.15 * scores["policy_compliance"], 3)
    revisions = state.get("revisions", 0)
    needs_revision = bool(unsupported) and revisions < 1
    evaluation = {"scores": scores, "overall": overall, "passed": overall >= 0.7 and compliance_ok and not unsupported,
                  "claims_checked": len(details), "unsupported": [d for d in details if not d["ok"]],
                  "unsupported_ids": unsupported, "missing_sections": [r for r in required if r not in present],
                  "revised": revisions > 0, "method": "lexical support of each cited claim against its cited passage; "
                                                      "section completeness; tool success; approval coverage"}
    await record(ctx, "evaluation", "deterministic", "deterministic-demo-v1", estimate_tokens(draft["markdown"]), 40, 0, True)
    ctx.emit({"type": "evaluation", **{k: evaluation[k] for k in ("scores", "overall", "passed", "claims_checked")},
              "unsupported": len(unsupported), "action": "revise" if needs_revision else "accept"})
    return {"evaluation": evaluation, "revisions": revisions + 1 if needs_revision else revisions,
            "route": "synthesizer" if needs_revision else "finalize"}


@node("finalize", "supervisor")
async def finalize(state: AgentState, ctx: RunCtx) -> dict:
    final = {**state["draft"], "evaluation": state["evaluation"], "guardrails": state.get("guardrails") or [],
             "plan": state.get("plan"), "dry_run": ctx.dry_run}
    await memory.event(ctx.workspace_id, "episodic", state.get("intent") or "run",
                       {"run_id": ctx.run_id, "request": state["request"][:300], "summary": final["summary"][:400]})
    return {"final": final}


__all__ = ["intake", "supervisor", "compliance", "approval_gate", "revise_actions", "execute_actions", "synthesizer",
           "evaluator", "finalize", "after_compliance", "after_approval", "settings"]
