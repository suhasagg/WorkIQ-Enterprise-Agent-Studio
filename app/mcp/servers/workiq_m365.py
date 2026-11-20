"""Demo Work IQ MCP server: the 10-tool, path-oriented Microsoft 365 vocabulary over synthetic data.

This simulates the *shape* of Work IQ MCP (generic operations + resource paths + runtime schema
introspection). It is not connected to Microsoft 365. Configure WORKIQ_MCP_ENDPOINT to use a real endpoint.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

from app.mcp.servers._base import (
    DESTRUCTIVE,
    EXTERNAL_WRITE,
    READ,
    WRITE,
    conn,
    create_record,
    get_record,
    records,
    run_server,
    ws,
)

mcp = FastMCP("workiq-m365-demo", instructions="Simulated Work IQ MCP. Data is synthetic.")
P = "m365"

CATALOG = {
    "/me/messages": {"kind": "message", "operations": ["fetch"], "description": "Mailbox messages"},
    "/me/events": {"kind": "event", "operations": ["fetch", "create", "update", "delete"], "description": "Calendar events"},
    "/me/onlineMeetings": {"kind": "meeting", "operations": ["fetch"], "description": "Online meetings"},
    "/me/onlineMeetings/{id}/transcript": {"kind": "meeting", "operations": ["fetch"], "description": "Meeting transcript"},
    "/me/chats/{id}/messages": {"kind": "chat_message", "operations": ["fetch", "create"], "description": "Teams chat messages"},
    "/me/drive/root/children": {"kind": "file", "operations": ["fetch"], "description": "OneDrive files"},
    "/users": {"kind": "person", "operations": ["fetch"], "description": "Directory users"},
    "/me/sendMail": {"kind": "sent_mail", "operations": ["action"], "description": "Send an email as the signed-in user"},
    "/search/query": {"kind": "*", "operations": ["function"], "description": "Search across workplace content"},
}
SCHEMAS = {
    "/me/sendMail": {"type": "object", "required": ["message"], "properties": {"message": {"type": "object", "required": ["subject", "toRecipients"], "properties": {
        "subject": {"type": "string"}, "body": {"type": "object"}, "toRecipients": {"type": "array", "items": {"type": "string"}}}}}},
    "/me/events": {"type": "object", "required": ["subject"], "properties": {"subject": {"type": "string"}, "start": {"type": "string"},
                   "end": {"type": "string"}, "attendees": {"type": "array", "items": {"type": "string"}}}},
    "/me/chats/{id}/messages": {"type": "object", "required": ["content"], "properties": {"content": {"type": "string"}}},
}


def _strip(url: str) -> tuple[str, int]:
    top = re.search(r"\$top=(\d+)", url)
    return url.split("?")[0].rstrip("/"), int(top.group(1)) if top else 25


def _fetch_one(url: str, context: dict | None) -> dict:
    path, top = _strip(url)
    m = re.fullmatch(r"/me/onlineMeetings/([^/]+)/transcript", path)
    if m:
        rec = get_record(context, P, m.group(1))
        if not rec:
            return {"url": url, "statusCode": 404, "error": "meeting not found"}
        return {"url": url, "statusCode": 200, "data": {"id": rec["id"], "subject": rec.get("subject"), "start": rec.get("start"),
                                                         "attendees": rec.get("attendees"), "transcript": rec.get("transcript")}}
    if re.fullmatch(r"/me/chats/[^/]+/messages", path):
        return {"url": url, "statusCode": 200, "data": records(context, P, "chat_message")[-top:]}
    entry = CATALOG.get(path)
    if not entry or "fetch" not in entry["operations"]:
        return {"url": url, "statusCode": 404, "error": "unsupported path; use search_paths"}
    rows = records(context, P, entry["kind"])
    if entry["kind"] == "meeting":
        rows = [{k: v for k, v in r.items() if k != "transcript"} for r in rows]
    return {"url": url, "statusCode": 200, "data": rows[-top:]}


@mcp.tool(annotations=READ)
def fetch(entityUrls: list[str], context: dict | None = None) -> dict:
    """Read one or more Microsoft 365 resources by path, e.g. /me/messages?$top=10."""
    return {"results": [_fetch_one(u, context) for u in entityUrls[:10]], "simulated": True}


@mcp.tool(annotations=WRITE)
def create_entity(entityUrl: str, entity: dict, context: dict | None = None) -> dict:
    """Create an entity at a collection path, e.g. a calendar event at /me/events."""
    path, _ = _strip(entityUrl)
    if path == "/me/events":
        return create_record(context, P, "event", "EVT", {**entity, "created_at": datetime.now(UTC).isoformat()})
    if re.fullmatch(r"/me/chats/[^/]+/messages", path):
        return create_record(context, P, "chat_message", "MSG", {**entity, "from": (context or {}).get("actor"),
                                                                 "at": datetime.now(UTC).isoformat()})
    raise ValueError(f"create not supported for {path}")


@mcp.tool(annotations=WRITE)
def update_entity(entityUrl: str, entity: dict, context: dict | None = None) -> dict:
    """Update an entity, e.g. /me/events/{id}."""
    rec_id = _strip(entityUrl)[0].rsplit("/", 1)[-1]
    cur = get_record(context, P, rec_id)
    if not cur:
        raise ValueError(f"{rec_id} not found")
    if (context or {}).get("dry_run"):
        return {**cur, **entity, "dry_run": True}
    from psycopg.types.json import Jsonb
    payload = {k: v for k, v in {**cur, **entity}.items() if k not in ("id", "kind")}
    conn().execute("UPDATE demo_records SET payload=%s WHERE id=%s AND workspace_id=%s", (Jsonb(payload), rec_id, ws(context)))
    return {"id": rec_id, **payload}


@mcp.tool(annotations=DESTRUCTIVE)
def delete_entity(entityUrl: str, context: dict | None = None) -> dict:
    """Delete an entity, e.g. /me/events/{id}."""
    rec_id = _strip(entityUrl)[0].rsplit("/", 1)[-1]
    if (context or {}).get("dry_run"):
        return {"deleted": rec_id, "dry_run": True}
    n = conn().execute("DELETE FROM demo_records WHERE id=%s AND workspace_id=%s AND provider=%s", (rec_id, ws(context), P)).rowcount
    return {"deleted": rec_id, "found": bool(n)}


@mcp.tool(annotations=EXTERNAL_WRITE)
def do_action(entityUrl: str, body: dict, context: dict | None = None) -> dict:
    """Invoke an action path. Supported: /me/sendMail with {message:{subject, body, toRecipients}}."""
    path, _ = _strip(entityUrl)
    if path != "/me/sendMail":
        raise ValueError(f"action not supported for {path}")
    msg = body.get("message") or {}
    if not msg.get("subject") or not msg.get("toRecipients"):
        raise ValueError("message.subject and message.toRecipients are required")
    rec = create_record(context, P, "sent_mail", "MAIL", {**msg, "sent_at": datetime.now(UTC).isoformat()})
    return {"status": "accepted", "messageId": rec["id"], "deduplicated": rec.get("deduplicated", False),
            "dry_run": rec.get("dry_run", False), "simulated": True}


@mcp.tool(annotations=READ)
def call_function(entityUrl: str, body: dict | None = None, context: dict | None = None) -> dict:
    """Call a function path. Supported: /search/query with {requests:[{query:{queryString}}]}."""
    path, _ = _strip(entityUrl)
    if path != "/search/query":
        raise ValueError(f"function not supported for {path}")
    q = ""
    try:
        q = body["requests"][0]["query"]["queryString"]
    except Exception:
        q = str((body or {}).get("query", ""))
    terms = [t for t in re.findall(r"[a-z0-9]{4,}", q.lower())]
    hits = []
    for kind in ("message", "file", "meeting", "event"):
        for r in records(context, P, kind):
            text = " ".join(str(v) for k, v in r.items() if k != "transcript").lower()
            score = sum(1 for t in terms if t in text)
            if score:
                hits.append({"kind": kind, "score": score, "id": r["id"], "title": r.get("subject") or r.get("name"),
                             "summary": (r.get("bodyPreview") or r.get("summary") or "")[:200]})
    hits.sort(key=lambda h: -h["score"])
    return {"hits": hits[:10], "simulated": True}


@mcp.tool(annotations=READ)
def ask(prompt: str, context: dict | None = None) -> dict:
    """Ask the workplace reasoning endpoint. In this simulation it returns a deterministic digest of matching content."""
    res = call_function("/search/query", {"requests": [{"query": {"queryString": prompt}}]}, context)
    lines = [f"- {h['title']}: {h['summary']}" for h in res["hits"][:5]]
    return {"answer": "Relevant workplace items:\n" + "\n".join(lines) if lines else "No matching workplace content found.",
            "sources": res["hits"][:5], "simulated": True}


@mcp.tool(annotations=READ)
def list_agents(context: dict | None = None) -> dict:
    """List declarative agents available to the signed-in user (none in the simulation)."""
    return {"agents": [], "simulated": True}


@mcp.tool(annotations=READ)
def get_schema(path: str, operationType: str = "fetch", format: str = "jsonschema", context: dict | None = None) -> dict:
    """Return the JSON schema for a resource path and operation."""
    return {"path": path, "operationType": operationType, "format": format,
            "schema": SCHEMAS.get(path, {"type": "object", "properties": {}}), "simulated": True}


@mcp.tool(annotations=READ)
def search_paths(filter: str = "", context: dict | None = None) -> dict:
    """Discover supported resource paths matching a filter."""
    f = filter.lower().replace(".*", "")
    return {"paths": [{"path": p, "operations": v["operations"], "description": v["description"]}
                      for p, v in CATALOG.items() if not f or f in p.lower() or f in v["description"].lower()],
            "simulated": True}


if __name__ == "__main__":
    run_server(mcp)
