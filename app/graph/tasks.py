"""Task-automation agent — action subgraph: gather -> extract -> propose. Also hosts the communication agent node.

Agents only *propose* state-changing actions. Nothing is executed here: proposals go through the compliance agent,
the human approval gate and the governed MCP gateway.
"""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta

from app.context import RunCtx
from app.graph.runtime import StepError, linear_subgraph, node
from app.graph.state import AgentState, current_step
from app.llm.model import compose
from app.mcp.gateway import call_tool
from app.rag.embeddings import tokenize

AGENT = "task"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
LEADERSHIP = "leadership@meridian.example"


def action(run_id: str, kind: str, server: str, tool: str, params: dict, title: str, rationale: str, agent: str,
           evidence: list[str] | None = None, source: str | None = None) -> dict:
    aid = "A" + hashlib.sha256(f"{run_id}|{kind}|{server}|{tool}|{source or title}".encode()).hexdigest()[:10]
    return {"id": aid, "kind": kind, "server": server, "tool": tool, "params": params, "title": title,
            "rationale": rationale, "agent": agent, "evidence": evidence or [], "status": "proposed", "risk": "medium",
            "round": 0, "source": source}


def resolve_due(text: str, anchor: datetime) -> str:
    """Resolve 'by Friday' / 'by the 24th' / 'on Thursday' / 'starting Monday' relative to the meeting date."""
    m = re.search(r"\b(?:by|on|starting)\s+(?:the\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2})(?:st|nd|rd|th)?\b",
                  text, re.I)
    if not m:
        return ""
    token = m.group(1).lower()
    if token.isdigit():
        day = int(token)
        d = anchor.date()
        for _ in range(62):
            if d.day == day and d >= anchor.date():
                return d.isoformat()
            d += timedelta(days=1)
        return ""
    delta = (WEEKDAYS.index(token) - anchor.weekday()) % 7 or 7
    return (anchor.date() + timedelta(days=delta)).isoformat()


def parse_transcript(transcript: str, anchor: datetime) -> dict:
    decisions, items, questions, speakers = [], [], [], []
    for line in transcript.splitlines():
        m = re.match(r"^([A-Z][\w.'-]+(?: [A-Z][\w.'-]+)+):\s*(.+)$", line.strip())
        if not m:
            continue
        speaker, text = m.group(1), m.group(2)
        if speaker not in speakers:
            speakers.append(speaker)
        for sent in re.split(r"(?<=[.?!])\s+", text):
            s = sent.strip()
            low = s.lower()
            if low.startswith("decision:"):
                decisions.append({"id": f"D{len(decisions) + 1}", "text": s.split(":", 1)[1].strip(), "by": speaker, "quote": s})
            elif low.startswith("open question:"):
                questions.append({"id": f"Q{len(questions) + 1}", "text": s.split(":", 1)[1].strip(), "by": speaker})
            elif re.search(r"\bI will\b", s) or low.startswith("action for me:"):
                task = re.sub(r"^(action for me:\s*)?(I will (also )?)", "", s, flags=re.I).strip().rstrip(".")
                task = re.sub(r"^I will (also )?", "", task, flags=re.I)
                items.append({"id": f"AI{len(items) + 1}", "owner": speaker, "task": task[:1].upper() + task[1:],
                              "due_date": resolve_due(s, anchor), "quote": s})
    return {"decisions": decisions, "action_items": items, "open_questions": questions, "speakers": speakers}


# ---------------------------------------------------------------- gather
@node("task.gather", AGENT, step=True)
async def gather(state: AgentState, ctx: RunCtx) -> dict:
    step = current_step(state)
    mode = step.get("mode")
    scratch: dict = {"mode": mode}
    if mode == "meeting_extract":
        mid = step.get("params", {}).get("meeting_id")
        if not mid:
            res = await call_tool(ctx, "workiq", "fetch", {"entityUrls": ["/me/onlineMeetings?$top=25"]})
            if res["status"] != "ok":
                raise StepError(f"workiq.fetch {res['status']}: {res.get('reason')}")
            meetings = (res["result"].get("results") or [{}])[0].get("data") or []
            if not meetings:
                raise StepError("no meetings available through Work IQ")
            q = set(tokenize(state["request"]))
            mid = max(meetings, key=lambda m: (len(q & set(tokenize(m.get("subject", "")))), m.get("start", "")))["id"]
        res = await call_tool(ctx, "workiq", "fetch", {"entityUrls": [f"/me/onlineMeetings/{mid}/transcript"]})
        if res["status"] != "ok":
            raise StepError(f"workiq.fetch {res['status']}: {res.get('reason')}")
        item = (res["result"].get("results") or [{}])[0]
        if item.get("statusCode") != 200 or not (item.get("data") or {}).get("transcript"):
            raise StepError(f"transcript for meeting {mid} is not available")
        scratch["meeting"] = item["data"]
    elif mode == "workplace_lookup":
        text = state["request"].lower()
        paths = [p for kw, p in (("mail", "/me/messages?$top=5"), ("inbox", "/me/messages?$top=5"), ("unread", "/me/messages?$top=5"),
                                 ("calendar", "/me/events?$top=5"), ("schedule", "/me/events?$top=5"), ("meeting", "/me/events?$top=5"),
                                 ("file", "/me/drive/root/children?$top=5"), ("chat", "/me/chats/all/messages?$top=5")) if kw in text]
        paths = list(dict.fromkeys(paths)) or ["/me/messages?$top=5", "/me/events?$top=5"]
        res = await call_tool(ctx, "workiq", "fetch", {"entityUrls": paths})
        if res["status"] != "ok":
            raise StepError(f"workiq.fetch {res['status']}: {res.get('reason')}")
        scratch["lookup"] = res["result"].get("results", [])
    return {"scratch": scratch, "step_result": None}


# ---------------------------------------------------------------- extract
@node("task.extract", AGENT, step=True)
async def extract(state: AgentState, ctx: RunCtx) -> dict:
    scratch = state["scratch"]
    if scratch["mode"] == "meeting_extract":
        m = scratch["meeting"]
        try:
            anchor = datetime.fromisoformat(m.get("start"))
        except (TypeError, ValueError):
            anchor = datetime.now(UTC)
        parsed = parse_transcript(m["transcript"], anchor)
        await compose(ctx, "meeting_extraction", m["transcript"],
                             f"{len(parsed['decisions'])} decisions, {len(parsed['action_items'])} action items")
        ctx.emit({"type": "extraction", "decisions": len(parsed["decisions"]), "action_items": len(parsed["action_items"]),
                  "open_questions": len(parsed["open_questions"])})
        meeting = {"id": m["id"], "subject": m.get("subject"), "start": m.get("start"), "attendees": m.get("attendees") or parsed["speakers"]}
        return {"extracted": {"meeting": meeting, **parsed},
                "step_result": {"status": "completed", "summary": f"{m.get('subject')}: {len(parsed['decisions'])} decisions, "
                                                                f"{len(parsed['action_items'])} action items"}}
    if scratch["mode"] == "workplace_lookup":
        items = []
        for r in scratch.get("lookup", []):
            for d in (r.get("data") or [])[:5]:
                items.append({"path": r["url"].split("?")[0], "id": d.get("id"),
                              "title": d.get("subject") or d.get("name") or d.get("content", "")[:80],
                              "detail": d.get("bodyPreview") or d.get("summary") or d.get("start") or "", "from": d.get("from")})
        return {"workplace": {"items": items}, "step_result": {"status": "completed", "summary": f"{len(items)} workplace items"}}
    return {}


# ---------------------------------------------------------------- propose
@node("task.propose", AGENT, step=True)
async def propose(state: AgentState, ctx: RunCtx) -> dict:
    step = current_step(state)
    mode, run_id = step.get("mode"), state["run_id"]
    findings = state.get("findings") or []
    out: list[dict] = []
    if mode == "meeting_actions":
        ex = state.get("extracted") or {}
        subject = (ex.get("meeting") or {}).get("subject", "meeting")
        for it in ex.get("action_items", []):
            out.append(action(run_id, "create_task", "tracker", "create_issue", {
                "title": it["task"][:120], "assignee": it["owner"], "due_date": it["due_date"], "priority": "medium",
                "description": f"Action item from '{subject}'. Source quote: \"{it['quote']}\"", "labels": ["meeting-action"]},
                f"Create task for {it['owner']}: {it['task'][:70]}",
                f"{it['owner']} committed to this in the meeting" + (f" with a due date of {it['due_date']}." if it["due_date"] else "."),
                AGENT, source=it["id"]))
    elif mode == "wbr_followups":
        for a in (state.get("analytics") or {}).get("anomalies", [])[:3]:
            cites = [c for f in findings if f.get("ref") == a["metric"] for c in f["citations"]][:2]
            ctx_text = next((f["text"] for f in findings if f.get("ref") == a["metric"]), "")
            out.append(action(run_id, "create_task", "tracker", "create_issue", {
                "title": f"Investigate {a['label']} ({a['gap_pct']:+.1f}% vs target)",
                "description": f"{a['label']} is {fmt(a['latest'], a['unit'])} against a target of {fmt(a['target'], a['unit'])} "
                               f"after {a['adverse_streak']} adverse week(s). Context: {ctx_text}".strip(),
                "assignee": "", "due_date": (datetime.now(UTC).date() + timedelta(days=7)).isoformat(),
                "priority": "high" if a["severity"] == "high" else "medium", "labels": ["wbr-follow-up", a["metric"]]},
                f"Create follow-up: investigate {a['label']}",
                f"{a['label']} is off target ({a['gap_pct']:+.1f}%) with a z-score of {a['zscore']}.", AGENT, cites, a["metric"]))
    elif mode == "escalations":
        an = state.get("analytics") or {}
        policy_cites = [c for f in findings if f.get("ref") == "policy" for c in f["citations"]][:2]
        for t in [t for t in an.get("breaches", []) if t["priority"] == "p1" and not t.get("escalated")][:3]:
            out.append(action(run_id, "escalate", "helpdesk", "escalate_ticket", {
                "ticket_id": t["id"], "tier": "tier3", "owner": "engineering-on-call",
                "reason": f"P1 open {t['age_hours']}h against a {t['sla_hours']}h SLA; customer {t['customer']} "
                          f"(ACV ${t['acv_usd']:,}). {t['subject']}."},
                f"Escalate {t['id']} ({t['customer']}) to tier 3",
                f"Priority-one ticket has breached its {t['sla_hours']}-hour SLA by {t['age_hours'] - t['sla_hours']}h.",
                AGENT, policy_cites, t["id"]))
        for c in [c for c in an.get("clusters", []) if c["recurring"]][:1]:
            cites = [x for f in findings if f.get("ref") == c["category"] for x in f["citations"]][:2]
            out.append(action(run_id, "create_task", "tracker", "create_issue", {
                "title": f"Problem record: {c['label']} ({c['count']} open tickets)",
                "description": f"Recurring problem across {len(c['customers'])} customers: {', '.join(c['customers'])}. "
                               f"Linked tickets: {', '.join(c['tickets'])}.",
                "assignee": "", "due_date": "", "priority": "high", "labels": ["problem-record", c["category"]]},
                f"Open problem record for {c['label']}",
                f"{c['count']} open tickets share this category, above the recurring-problem threshold of 3.", AGENT, cites,
                "problem-" + c["category"]))
    summary = f"{len(out)} action(s) proposed for approval" if out else "No actions to propose"
    if out:
        ctx.emit({"type": "actions_proposed", "actions": [{"id": a["id"], "title": a["title"], "tool": f"{a['server']}.{a['tool']}"} for a in out]})
    return {"proposed_actions": out, "step_result": {"status": "completed", "summary": summary}}


def fmt(v, unit: str) -> str:
    if v is None:
        return "n/a"
    if unit == "USD":
        return f"${v:,.0f}"
    if unit in ("%", "x", "h"):
        return f"{v:g}{unit}"
    return f"{v:g}" + (f" {unit}" if unit and unit != "count" else "")


def _route(state: AgentState) -> str:
    mode = state["scratch"].get("mode")
    return "extract" if mode in ("meeting_extract", "workplace_lookup") else "propose"


extract.terminal = True  # type: ignore[attr-defined]


def build():
    return linear_subgraph([("gather", gather), ("extract", extract), ("propose", propose)],
                           branches={"gather": (_route, {"extract": "extract", "propose": "propose"})}).compile()


# ---------------------------------------------------------------- communication agent
def _mail(run_id: str, to: list[str], subject: str, body: str, title: str, rationale: str, evidence: list[str], source: str) -> dict:
    return action(run_id, "send_email", "workiq", "do_action",
                  {"entityUrl": "/me/sendMail", "body": {"message": {"subject": subject, "toRecipients": to,
                                                                    "body": {"contentType": "Text", "content": body}}}},
                  title, rationale, "communication", evidence, source)


@node("communication", "communication", step=True)
async def communication(state: AgentState, ctx: RunCtx) -> dict:
    step = current_step(state)
    mode, run_id = step.get("mode"), state["run_id"]
    an, findings = state.get("analytics") or {}, state.get("findings") or []
    out: list[dict] = []
    if mode == "wbr_email":
        lines = [f"- {a['label']}: {fmt(a['latest'], a['unit'])} vs target {fmt(a['target'], a['unit'])} ({a['gap_pct']:+.1f}%)"
                 for a in an.get("anomalies", [])[:5]]
        body = "Weekly business review summary.\n\nKPIs needing attention:\n" + ("\n".join(lines) or "- none") + \
               "\n\nThe full cited report is available in Work IQ Agent Studio."
        body = await compose(ctx, "draft_email", body, body, "Rewrite as a concise executive email. Keep all numbers.")
        out.append(_mail(run_id, [LEADERSHIP], "Weekly business review: KPIs needing attention", body,
                         "Send WBR summary to leadership", "Leadership distribution list receives the weekly review.", [], "wbr_email"))
    elif mode == "customer_update":
        top = next((c for c in an.get("clusters", []) if c["recurring"]), None)
        if top:
            fix = next((f for f in findings if f.get("ref") == top["category"]), None)
            body = (f"We are investigating {top['label'].lower()} affecting your account. Our engineering on-call team is "
                    f"engaged and we will send the next update within two hours.")
            if fix:
                body += f"\n\nCurrent guidance: {fix['text']}"
            body = await compose(ctx, "draft_email", body, body, "Rewrite as a clear customer status update.")
            out.append(_mail(run_id, ["support-duty-manager@meridian.example"], f"Customer status update draft: {top['label']}",
                             body, f"Send customer update draft for {top['label']} to the duty manager",
                             "Policy requires duty-manager approval before customer communication is sent.",
                             (fix or {}).get("citations", []) + [c for f in findings if f.get("ref") == "comms" for c in f["citations"]][:1],
                             "customer_update"))
    elif mode == "meeting_followup":
        ex = state.get("extracted") or {}
        m = ex.get("meeting") or {}
        body = f"Follow-up from {m.get('subject', 'the meeting')}.\n\nDecisions:\n" + \
               "\n".join(f"- {d['text']}" for d in ex.get("decisions", [])) + "\n\nAction items:\n" + \
               "\n".join(f"- {i['owner']}: {i['task']}" + (f" (due {i['due_date']})" if i["due_date"] else "") for i in ex.get("action_items", []))
        if ex.get("open_questions"):
            body += "\n\nOpen questions:\n" + "\n".join(f"- {q['text']}" for q in ex["open_questions"])
        body = await compose(ctx, "draft_email", body, body, "Rewrite as a concise meeting follow-up. Keep owners and dates.")
        to = [n.lower().replace(" ", ".") + "@meridian.example" for n in m.get("attendees", [])]
        if to:
            out.append(_mail(run_id, to, f"Follow-up: {m.get('subject')}", body, f"Send follow-up summary to {len(to)} attendees",
                             "Attendees receive decisions and owners after the meeting.", [], "meeting_followup"))
    elif mode == "adhoc_email":
        req = state["request"]
        to = re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", req)
        m = re.search(r"\b(?:saying|that says|about|:)\s*(.+)$", req, re.I | re.S)
        content = (m.group(1) if m else req).strip().strip('"')
        body = await compose(ctx, "draft_email", content, content, "Write a short professional email with this content.")
        if not to:
            raise StepError("no recipient email address found in the request")
        out.append(_mail(run_id, to, content.split(".")[0][:70] or "Message", body, f"Send email to {', '.join(to)}",
                         "Requested directly by the user.", [], "adhoc_email"))
    if out:
        ctx.emit({"type": "actions_proposed", "actions": [{"id": a["id"], "title": a["title"], "tool": f"{a['server']}.{a['tool']}"} for a in out]})
    return {"proposed_actions": out,
            "step_result": {"status": "completed", "summary": f"{len(out)} communication draft(s) prepared" if out else "No communication needed"}}
