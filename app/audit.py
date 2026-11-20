"""Tamper-evident, hash-chained audit log persisted in PostgreSQL (one chain per workspace)."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from app import db
from app.redaction import redact


def _digest(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


class AuditLog:
    async def append(self, event: dict) -> dict:
        """event keys: workspace_id, actor, action, resource, decision, run_id, detail"""
        ws = event.get("workspace_id") or event.get("tenant") or "system"
        async with db.pool().connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"audit:{ws}",))
            cur = await conn.execute(
                "SELECT hash FROM audit_events WHERE workspace_id=%s ORDER BY seq DESC LIMIT 1", (ws,))
            row = await cur.fetchone()
            prior = row["hash"] if row else "GENESIS"
            body = {
                "ts": datetime.now(UTC).isoformat(), "workspace_id": ws, "actor": event.get("actor") or event.get("subject") or "system",
                "action": event.get("action", "event"), "resource": event.get("resource", ""),
                "decision": event.get("decision", ""), "run_id": event.get("run_id"),
                "detail": redact(event.get("detail") or {}), "prior": prior,
            }
            body["hash"] = _digest(body)
            await conn.execute(
                """INSERT INTO audit_events(workspace_id, ts, actor, action, resource, decision, run_id, detail, prior_hash, hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (ws, body["ts"], body["actor"], body["action"], body["resource"], body["decision"],
                 body["run_id"], db.J(body["detail"]), prior, body["hash"]))
            return body

    async def list(self, workspace_id: str, limit: int = 200, run_id: str | None = None) -> list[dict]:
        if run_id:
            return await db.fetch_all(
                "SELECT * FROM audit_events WHERE workspace_id=%s AND run_id=%s ORDER BY seq DESC LIMIT %s",
                (workspace_id, run_id, limit))
        return await db.fetch_all(
            "SELECT * FROM audit_events WHERE workspace_id=%s ORDER BY seq DESC LIMIT %s", (workspace_id, limit))

    async def verify(self, workspace_id: str) -> dict:
        rows = await db.fetch_all("SELECT * FROM audit_events WHERE workspace_id=%s ORDER BY seq", (workspace_id,))
        prior = "GENESIS"
        for r in rows:
            body = {"ts": r["ts"], "workspace_id": r["workspace_id"],
                    "actor": r["actor"], "action": r["action"], "resource": r["resource"], "decision": r["decision"],
                    "run_id": r["run_id"], "detail": r["detail"], "prior": prior}
            if r["prior_hash"] != prior or _digest(body) != r["hash"]:
                return {"valid": False, "broken_at_seq": r["seq"], "events": len(rows)}
            prior = r["hash"]
        return {"valid": True, "events": len(rows)}


audit = AuditLog()
