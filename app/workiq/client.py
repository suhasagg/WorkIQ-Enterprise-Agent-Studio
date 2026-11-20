"""Work IQ client restricted to the 10-tool vocabulary.

Originally a JSON-RPC client with inline fixtures; it now routes through the governed MCP gateway to the
registered `workiq` MCP server (the local demo server by default, or WORKIQ_MCP_ENDPOINT when configured).
"""
from __future__ import annotations

from app.context import RunCtx
from app.mcp.gateway import gateway
from app.mcp.manager import mcp_manager

TEN_TOOLS = {"fetch", "create_entity", "update_entity", "delete_entity", "do_action", "call_function",
             "ask", "list_agents", "get_schema", "search_paths"}


class WorkIQClient:
    @property
    def local(self) -> bool:
        conn = mcp_manager.get("workiq")
        return bool(conn and conn.cfg["kind"] == "demo")

    async def call(self, name: str, args: dict, ctx: RunCtx, **kw) -> dict:
        if name not in TEN_TOOLS:
            raise ValueError("unsupported Work IQ MCP tool")
        res = await gateway.call(ctx, "workiq", name, args, **kw)
        if res["status"] == "ok":
            return {"status": "ok", "structuredContent": res["result"], "local": self.local}
        return {"status": res["status"], "reason": res.get("reason"), "local": self.local}


workiq = WorkIQClient()
