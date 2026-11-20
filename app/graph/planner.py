"""Intent classification and plan construction for the supervisor."""
from __future__ import annotations

import re

WORKFLOWS: dict[str, dict] = {
    "weekly_business_review": {
        "name": "Weekly Executive Business Review",
        "description": "Analyse weekly KPIs, detect anomalies, retrieve business context, propose follow-ups and produce a cited executive report.",
        "prompt": "Prepare this week's executive business review with KPI anomalies, likely causes and recommended follow-up actions.",
        "agents": ["analytics", "research", "task", "communication", "compliance", "synthesizer", "evaluator"],
        "requires_approval": True,
    },
    "support_escalation": {
        "name": "Customer Support Escalation",
        "description": "Analyse open tickets, find recurring problems and SLA breaches, retrieve known fixes, recommend escalations and draft customer communication.",
        "prompt": "Review open priority-one support tickets, identify recurring problems and SLA breaches, and recommend escalations.",
        "agents": ["analytics", "research", "task", "communication", "compliance", "synthesizer", "evaluator"],
        "requires_approval": True,
    },
    "knowledge_research": {
        "name": "Enterprise Knowledge Research",
        "description": "Answer a question from indexed documents with hybrid retrieval, citations and a groundedness check.",
        "prompt": "Summarize the major operational risks from our internal documents and cite the evidence.",
        "agents": ["research", "synthesizer", "evaluator"],
        "requires_approval": False,
    },
    "meeting_to_execution": {
        "name": "Meeting-to-Execution Automation",
        "description": "Read a meeting transcript, extract decisions and action items with owners and dates, then create tracked tasks and a follow-up after approval.",
        "prompt": "Turn the latest Project Atlas weekly sync into tracked action items and a follow-up summary.",
        "agents": ["task", "research", "communication", "compliance", "synthesizer", "evaluator"],
        "requires_approval": True,
    },
}

INTENT_RULES = [
    ("meeting_to_execution", r"\b(meeting|transcript|action items?|minutes|follow[- ]?ups? from)\b"),
    ("support_escalation", r"\b(tickets?|escalat\w*|sla|support (queue|backlog)|helpdesk|priority[- ]one|p1)\b"),
    ("weekly_business_review", r"\b(kpis?|business review|wbr|metrics?|revenue|arr|churn rate|uptime trend|anomal\w*|dashboard numbers)\b"),
    ("send_email", r"\b(send|email|e-mail|mail)\b.*\bto\b.*@"),
    ("workplace_lookup", r"\b(my (inbox|emails?|mail|calendar|meetings|files|chats?)|unread|schedule today)\b"),
]


QUESTION = r"^(when|why|how)\b|\b(policy|policies|according to)\b"


def classify(request: str) -> str:
    text = request.lower().strip()
    if re.search(QUESTION, text) and not re.search(INTENT_RULES[0][1], text):
        return "knowledge_research"  # a question about how things work is research, not an operational workflow
    for intent, pattern in INTENT_RULES:
        if re.search(pattern, text):
            return intent
    return "knowledge_research"


def _step(i: int, agent: str, title: str, mode: str, optional: bool = False, **params) -> dict:
    return {"id": f"s{i}", "agent": agent, "title": title, "mode": mode, "params": params, "status": "pending",
            "attempts": 0, "error": None, "summary": None, "optional": optional}


FILLER = re.compile(r"\b(please|summari[sz]e|explain|describe|list|tell me( about)?|what (are|is)|give me|cite( the)?( evidence| sources)?|"
                    r"from (our|the) (internal )?(documents?|docs|knowledge base)|and cite.*$|according to (our|the) (documents?|docs))\b", re.I)


def clean_query(q: str) -> str:
    out = re.sub(r"\s+", " ", FILLER.sub(" ", q)).strip(" ?.,")
    return out if len(out) >= 6 else q.strip(" ?.")


def decompose(question: str) -> list[str]:
    """Split a compound question into focused retrieval queries (deterministic)."""
    q = re.sub(r"\s+", " ", question.strip())
    parts = [p.strip(" ?.") for p in re.split(r"\?|;|\band also\b|\. ", q) if len(p.strip(" ?.")) > 12]
    out: list[str] = []
    for p in [clean_query(x) for x in [q] + parts]:
        if p and p.lower() not in [o.lower() for o in out]:
            out.append(p)
    return out[:4]


def build_plan(request: str, workflow: str | None, options: dict | None = None) -> tuple[str, list[dict]]:
    options = options or {}
    intent = workflow if workflow in WORKFLOWS else classify(request)
    if intent == "weekly_business_review":
        steps = [
            _step(1, "analytics", "Analyse weekly KPIs and detect anomalies", "kpi", weeks=int(options.get("weeks", 12))),
            _step(2, "research", "Retrieve business context for anomalies", "anomaly_context", optional=True),
            _step(3, "task", "Propose follow-up work items", "wbr_followups", optional=True),
            _step(4, "communication", "Draft leadership summary email", "wbr_email", optional=True),
        ]
    elif intent == "support_escalation":
        steps = [
            _step(1, "analytics", "Analyse open tickets, SLA breaches and recurring problems", "tickets"),
            _step(2, "research", "Retrieve known fixes and escalation policy", "ticket_context", optional=True),
            _step(3, "task", "Recommend escalations", "escalations", optional=True),
            _step(4, "communication", "Draft customer status update", "customer_update", optional=True),
        ]
    elif intent == "meeting_to_execution":
        steps = [
            _step(1, "task", "Read transcript and extract decisions and action items", "meeting_extract",
                  meeting_id=options.get("meeting_id")),
            _step(2, "research", "Retrieve related internal context", "meeting_context", optional=True),
            _step(3, "task", "Propose tracked tasks for action items", "meeting_actions", optional=True),
            _step(4, "communication", "Draft follow-up summary", "meeting_followup", optional=True),
        ]
    elif intent == "send_email":
        steps = [_step(1, "communication", "Draft the requested email", "adhoc_email")]
    elif intent == "workplace_lookup":
        steps = [_step(1, "task", "Look up workplace data through Work IQ", "workplace_lookup")]
    else:
        intent = "knowledge_research"
        steps = [_step(1, "research", "Retrieve and grade evidence", "question", queries=decompose(request), k=6)]
    return intent, steps
