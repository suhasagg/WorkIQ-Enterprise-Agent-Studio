"""Deterministic synthetic demo data. Everything here is fictional and labelled as seeded/simulated in the UI."""
from __future__ import annotations

import math
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from app import db
from app.config import settings
from app.rag.ingest import create_document, ingest_document
from app.security import hash_password

DATA = Path(__file__).parent / "data"
WORKSPACES = {"meridian": "Meridian Dynamics", "globex": "Globex Retail"}
USERS = [
    ("usr_admin", "meridian", "admin@meridian.example", "Avery Chen", "admin"),
    ("usr_approver", "meridian", "approver@meridian.example", "Priya Raman", "approver"),
    ("usr_operator", "meridian", "operator@meridian.example", "Jordan Blake", "operator"),
    ("usr_viewer", "meridian", "viewer@meridian.example", "Sam Okafor", "viewer"),
    ("usr_globex", "globex", "admin@globex.example", "Morgan Lee", "admin"),
]
RESTRICTED = {"board-compensation-memo.md": ["admin"]}

# metric: (start, end, target, noise, integer?)
KPI_SHAPE = {
    "net_new_arr": (182000, 131000, 170000, 6000, True),
    "pipeline_coverage": (3.3, 2.5, 3.0, 0.08, False),
    "gross_margin": (71.6, 72.5, 72.0, 0.2, False),
    "logo_churn": (6.1, 7.6, 6.0, 0.1, False),
    "nps": (44, 47, 45, 0.8, True),
    "uptime": (99.96, 99.90, 99.9, 0.015, False),
    "p1_open": (3, 8, 5, 0.6, True),
    "resolution_hours": (5.2, 7.9, 6.0, 0.25, False),
    "on_time_delivery": (88, 78, 90, 1.2, True),
    "cloud_cost": (41000, 44100, 45000, 500, True),
}

TICKETS = [
    ("SUP-1041", "p1", "open", "sso_login", "Northfield Logistics", 410000, 31, "SSO login fails with invalid SAML response for all users"),
    ("SUP-1042", "p1", "open", "sso_login", "Brightwater Health", 365000, 26, "Users locked out after identity provider certificate rotation"),
    ("SUP-1047", "p1", "open", "sso_login", "Castellan Bank", 520000, 9, "Intermittent SSO failures for the EU tenant"),
    ("SUP-1050", "p1", "pending", "data_sync", "Orchard Foods", 180000, 14, "Nightly data sync has not completed for two days"),
    ("SUP-1052", "p1", "open", "data_sync", "Northfield Logistics", 410000, 7, "Dashboards show stale shipment data after sync delay"),
    ("SUP-1055", "p1", "open", "payments", "Vertex Mobility", 275000, 5, "Invoice payment page returns an error for card payments"),
    ("SUP-1058", "p1", "open", "sso_login", "Pinecrest Energy", 150000, 3, "New users cannot complete first SSO login"),
    ("SUP-1060", "p1", "open", "billing_export", "Harbor Insurance", 240000, 2, "Monthly billing export contains duplicate invoice lines"),
    ("SUP-1031", "p2", "open", "data_sync", "Lumen Retail", 95000, 52, "Incremental sync slower than expected"),
    ("SUP-1036", "p2", "open", "billing_export", "Orchard Foods", 180000, 44, "Billing export missing tax column"),
    ("SUP-1044", "p2", "pending", "sso_login", "Kestrel Aviation", 130000, 20, "SSO session expires too quickly"),
    ("SUP-1049", "p2", "open", "notifications", "Lumen Retail", 95000, 12, "Push notifications arrive late"),
    ("SUP-1022", "p1", "resolved", "payments", "Castellan Bank", 520000, 120, "Payments unavailable during outage INC-2291"),
    ("SUP-1025", "p2", "resolved", "data_sync", "Harbor Insurance", 240000, 96, "Sync failed after schema change"),
    ("SUP-1062", "p3", "open", "notifications", "Pinecrest Energy", 150000, 1, "Request for notification digest option"),
]

TRANSCRIPT = """Project Atlas weekly sync — transcript (synthetic)
Dana Whitfield: Thanks everyone. Goal today is to confirm the Atlas beta date and unblock the sync partitioning work.
Ravi Menon: Sync partitioning is two weeks behind. We lost two engineers to the payments failover project.
Dana Whitfield: Decision: we move the Atlas beta start by two weeks to protect quality. Marketing needs to tell the design partners.
Elena Sokolova: I will draft the design partner update and send it by Friday.
Ravi Menon: I will deliver the partitioned sync for the ten largest tenants by the 24th. I need one more reviewer.
Dana Whitfield: Decision: Tomas joins the sync partitioning review rotation starting Monday.
Tomas Varga: Agreed. I will also publish the updated SSO knowledge articles by Wednesday so support is ready for beta.
Priya Raman: Identity certificate automation is on track. I will deploy it to staging by the 20th and share the rollout plan.
Dana Whitfield: Decision: SSO certificate automation is a hard launch criterion for the beta.
Elena Sokolova: Open question: do we offer service credits to the two accounts hit by SSO failures? Customer success wants an answer.
Dana Whitfield: Action for me: I will take the service credit question to the leadership review on Thursday.
Dana Whitfield: Summary. Beta moves two weeks. Ravi owns partitioned sync, Tomas joins review and owns knowledge articles, Priya owns certificate automation, Elena owns partner communication."""


def last_monday(today: date | None = None) -> date:
    today = today or datetime.now(UTC).date()
    return today - timedelta(days=today.weekday())


async def seed_identity() -> None:
    for wid, name in WORKSPACES.items():
        await db.execute("INSERT INTO workspaces(id,name) VALUES (%s,%s) ON CONFLICT (id) DO NOTHING", (wid, name))
    pw = hash_password(settings.demo_password)
    for uid, ws, email, name, role in USERS:
        await db.execute(
            """INSERT INTO users(id, workspace_id, email, name, role, password_hash) VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET password_hash=EXCLUDED.password_hash, role=EXCLUDED.role""",
            (uid, ws, email, name, role, pw))


async def seed_kpis(ws: str = "meridian", weeks: int = 12) -> None:
    rng = random.Random(42)
    start = last_monday() - timedelta(weeks=weeks)
    for i in range(weeks):
        t = i / (weeks - 1)
        ease = t * t * (3 - 2 * t)  # smooth drift: stable early weeks, deterioration in recent weeks
        for metric, (a, b, target, noise, integer) in KPI_SHAPE.items():
            v = a + (b - a) * ease + rng.uniform(-noise, noise) + (math.sin(i) * noise * 0.3)
            if metric == "uptime" and i == weeks - 4:
                v = 99.71  # the payments outage week
            v = round(v) if integer else round(v, 2)
            await db.execute(
                """INSERT INTO kpi_weekly(workspace_id, week_start, metric, value, target) VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (workspace_id, week_start, metric) DO UPDATE SET value=EXCLUDED.value, target=EXCLUDED.target""",
                (ws, start + timedelta(weeks=i), metric, v, target))


async def _record(rec_id: str, ws: str, provider: str, kind: str, payload: dict) -> None:
    await db.execute(
        """INSERT INTO demo_records(id, workspace_id, provider, kind, payload) VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (id) DO NOTHING""", (rec_id, ws, provider, kind, db.J({**payload, "simulated": True})))


async def seed_providers(ws: str = "meridian") -> None:
    now = datetime.now(UTC)
    for tid, pri, status, cat, customer, acv, age_h, subject in TICKETS:
        sla = {"p1": 4, "p2": 24, "p3": 72}[pri]
        await _record(tid, ws, "helpdesk", "ticket", {
            "priority": pri, "status": status, "category": cat, "customer": customer, "acv_usd": acv, "subject": subject,
            "age_hours": age_h, "sla_hours": sla, "sla_breached": status != "resolved" and age_h > sla,
            "opened_at": (now - timedelta(hours=age_h)).isoformat(), "escalated": False, "tier": "tier2" if pri == "p1" else "tier1"})
    issues = [("WIQ-SEED01", "Automate SSO certificate rotation", "Priya Raman", "in_progress", "high"),
              ("WIQ-SEED02", "Partitioned nightly sync for large tenants", "Ravi Menon", "in_progress", "high"),
              ("WIQ-SEED03", "Update rollback runbook for payments service", "Tomas Varga", "open", "medium")]
    for iid, title, who, status, pri in issues:
        await _record(iid, ws, "tracker", "issue", {"title": title, "description": "Seeded demo issue.", "assignee": who,
                                                   "status": status, "priority": pri, "labels": ["seed"], "due_date": ""})
    await _record("MTG-ATLAS-WEEKLY", ws, "m365", "meeting", {
        "subject": "Project Atlas weekly sync", "start": (now - timedelta(days=1)).isoformat(),
        "attendees": ["Dana Whitfield", "Ravi Menon", "Elena Sokolova", "Tomas Varga", "Priya Raman"],
        "summary": "Atlas beta date, sync partitioning, SSO automation", "transcript": TRANSCRIPT})
    mails = [("MAILIN-01", "Northfield Logistics: escalation on SSO outage", "cio@northfield.example",
              "Our users have been locked out twice this month. We need a root cause and a date for the permanent fix."),
             ("MAILIN-02", "Halcyon Freight Data contract renewal", "accounts@halcyon-freight.example",
              "Your telemetry contract expires in ninety days. Please confirm renewal terms."),
             ("MAILIN-03", "Weekly business review pre-read", "chief.of.staff@meridian.example",
              "Pre-read attached. Focus areas: ARR shortfall, churn, uptime and support backlog.")]
    for mid, subject, sender, preview in mails:
        await _record(mid, ws, "m365", "message", {"subject": subject, "from": sender, "bodyPreview": preview,
                                                   "receivedDateTime": (now - timedelta(hours=6)).isoformat()})
    events = [("EVTIN-01", "Leadership business review", 2), ("EVTIN-02", "Project Atlas weekly sync", 6),
              ("EVTIN-03", "Customer call: Brightwater Health", 1)]
    for eid, subject, days in events:
        await _record(eid, ws, "m365", "event", {"subject": subject, "start": (now + timedelta(days=days)).isoformat(),
                                                 "attendees": ["Dana Whitfield", "Priya Raman"]})
    people = [("PER-01", "Dana Whitfield", "Programme Director"), ("PER-02", "Ravi Menon", "Data Pipeline Lead"),
              ("PER-03", "Elena Sokolova", "Product Marketing Lead"), ("PER-04", "Tomas Varga", "Support Engineering Manager"),
              ("PER-05", "Priya Raman", "Director of Identity")]
    for pid, name, title in people:
        await _record(pid, ws, "m365", "person", {"name": name, "jobTitle": title,
                                                  "mail": name.lower().replace(" ", ".") + "@meridian.example"})
    await _record("FILE-01", ws, "m365", "file", {"name": "Q3 board deck.pptx", "summary": "Quarterly board presentation draft"})
    await _record("CHAT-01", ws, "m365", "chat_message", {"from": "Ravi Menon", "content": "Sync partitioning PR is ready for review."})


async def seed_documents() -> None:
    for ws in WORKSPACES:
        folder = DATA / ws
        if not folder.exists():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in (".md", ".txt", ".pdf"):
                continue
            data = path.read_bytes()
            doc, created = await create_document(
                workspace_id=ws, filename=path.name, data=data, created_by="seed", origin="seed",
                allowed_roles=RESTRICTED.get(path.name))
            if created or doc["status"] != "indexed":
                await ingest_document(doc["id"], data)


async def seed_all(history: bool = True) -> None:
    await seed_identity()
    await seed_kpis()
    await seed_providers()
    await seed_documents()
    if history:
        from app.seed.history import seed_history
        await seed_history()
