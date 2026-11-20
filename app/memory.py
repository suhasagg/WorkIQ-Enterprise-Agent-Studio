"""Workplace memory layers (working, conversation, episodic, semantic, entity), persisted per workspace."""
from __future__ import annotations

from app import db
from app.redaction import redact

LAYERS = ["working", "conversation", "episodic", "semantic", "entity"]


class WorkplaceMemory:
    async def event(self, tenant: str, layer: str, key: str, value) -> None:
        if layer not in LAYERS:
            raise ValueError("unknown memory layer")
        await db.execute("INSERT INTO memory_items(workspace_id, layer, key, value) VALUES (%s,%s,%s,%s)",
                         (tenant, layer, key, db.J(redact(value))))

    async def snapshot(self, tenant: str, limit: int = 50) -> dict:
        out = {}
        for layer in LAYERS:
            rows = await db.fetch_all(
                "SELECT key, value, ts FROM memory_items WHERE workspace_id=%s AND layer=%s ORDER BY id DESC LIMIT %s",
                (tenant, layer, limit))
            out[layer] = rows
        return out

    async def recall(self, tenant: str, layer: str, limit: int = 5) -> list[dict]:
        return await db.fetch_all(
            "SELECT key, value, ts FROM memory_items WHERE workspace_id=%s AND layer=%s ORDER BY id DESC LIMIT %s",
            (tenant, layer, limit))


memory = WorkplaceMemory()
