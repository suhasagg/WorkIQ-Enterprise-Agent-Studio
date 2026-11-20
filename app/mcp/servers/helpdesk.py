"""Demo support-desk MCP server (tickets, notes, escalations). Simulated provider backed by synthetic data."""
from __future__ import annotations

from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

from app.mcp.servers._base import (
    EXTERNAL_WRITE,
    READ,
    WRITE,
    create_record,
    get_record,
    records,
    run_server,
    update_record,
)

mcp = FastMCP("helpdesk-demo", instructions="Simulated support desk. Data is synthetic.")
P = "helpdesk"


@mcp.tool(annotations=READ)
def search_tickets(priority: str = "", status: str = "", category: str = "", limit: int = 50, context: dict | None = None) -> dict:
    """Search support tickets. priority: p1|p2|p3, status: open|pending|resolved."""
    out = []
    for r in records(context, P, "ticket"):
        if priority and r.get("priority") != priority:
            continue
        if status == "unresolved" and r.get("status") == "resolved":
            continue
        if status and status != "unresolved" and r.get("status") != status:
            continue
        if category and r.get("category") != category:
            continue
        out.append(r)
    return {"tickets": out[:limit], "total": len(out)}


@mcp.tool(annotations=READ)
def get_ticket(ticket_id: str, context: dict | None = None) -> dict:
    """Fetch one ticket with its notes."""
    r = get_record(context, P, ticket_id)
    if not r:
        raise ValueError(f"ticket {ticket_id} not found")
    r["notes"] = [n for n in records(context, P, "note") if n.get("ticket_id") == ticket_id]
    return r


@mcp.tool(annotations=WRITE)
def add_internal_note(ticket_id: str, note: str, context: dict | None = None) -> dict:
    """Attach an internal (not customer-visible) note to a ticket."""
    if not get_record(context, P, ticket_id):
        raise ValueError(f"ticket {ticket_id} not found")
    return create_record(context, P, "note", "NOTE", {"ticket_id": ticket_id, "note": note,
                                                      "at": datetime.now(UTC).isoformat()})


@mcp.tool(annotations=EXTERNAL_WRITE)
def escalate_ticket(ticket_id: str, tier: str = "tier3", reason: str = "", owner: str = "", context: dict | None = None) -> dict:
    """Escalate a ticket to a higher support tier and page the owning team."""
    if not get_record(context, P, ticket_id):
        raise ValueError(f"ticket {ticket_id} not found")
    esc = create_record(context, P, "escalation", "ESC", {"ticket_id": ticket_id, "tier": tier, "reason": reason,
                                                          "owner": owner, "at": datetime.now(UTC).isoformat()})
    if not esc.get("dry_run") and not esc.get("deduplicated"):
        update_record(context, P, ticket_id, {"escalated": True, "tier": tier})
    return esc


if __name__ == "__main__":
    run_server(mcp)
