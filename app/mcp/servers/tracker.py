"""Demo work-tracker MCP server (Jira-style issues). Simulated provider backed by synthetic data."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.mcp.servers._base import READ, WRITE, create_record, get_record, records, run_server, update_record

mcp = FastMCP("tracker-demo", instructions="Simulated work tracker. Data is synthetic.")
P = "tracker"


@mcp.tool(annotations=READ)
def search_issues(query: str = "", status: str = "", assignee: str = "", limit: int = 25, context: dict | None = None) -> dict:
    """Search issues by free text, status (open|in_progress|done) and assignee."""
    out = []
    for r in records(context, P, "issue"):
        if status and r.get("status") != status:
            continue
        if assignee and assignee.lower() not in str(r.get("assignee", "")).lower():
            continue
        if query and query.lower() not in (r.get("title", "") + " " + r.get("description", "")).lower():
            continue
        out.append(r)
    return {"issues": out[-limit:], "total": len(out)}


@mcp.tool(annotations=READ)
def get_issue(issue_id: str, context: dict | None = None) -> dict:
    """Fetch a single issue by id."""
    r = get_record(context, P, issue_id)
    if not r:
        raise ValueError(f"issue {issue_id} not found")
    return r


@mcp.tool(annotations=WRITE)
def create_issue(title: str, description: str = "", assignee: str = "", due_date: str = "", priority: str = "medium",
                 labels: list[str] | None = None, context: dict | None = None) -> dict:
    """Create a follow-up task/issue. Idempotent when the caller supplies an idempotency key."""
    if not title.strip():
        raise ValueError("title is required")
    return create_record(context, P, "issue", "WIQ", {
        "title": title.strip(), "description": description, "assignee": assignee, "due_date": due_date,
        "priority": priority, "labels": labels or [], "status": "open"})


@mcp.tool(annotations=WRITE)
def update_issue(issue_id: str, status: str = "", assignee: str = "", due_date: str = "", context: dict | None = None) -> dict:
    """Update status, assignee or due date of an issue."""
    patch = {k: v for k, v in {"status": status, "assignee": assignee, "due_date": due_date}.items() if v}
    return update_record(context, P, issue_id, patch)


if __name__ == "__main__":
    run_server(mcp)
