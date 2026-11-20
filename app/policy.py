"""Server-side policy engine: tool permissions, risk classification, DLP and approval requirements.

Extends the original path-aware Work IQ policy (read/write scopes, blocked segments, mutation approval)
to every MCP server. Decisions are made from server-declared tool annotations plus local overrides and
are never delegated to the model or the UI.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from app.redaction import find_sensitive

BLOCKED_SEGMENTS = ("/authentication/", "/servicePrincipals/", "/directoryRoles/", "/oauth2PermissionGrants/")
READ_TOOLS = {"fetch", "call_function", "ask", "list_agents", "get_schema", "search_paths"}
WRITE_TOOLS = {"create_entity", "update_entity", "delete_entity", "do_action"}
INTERNAL_DOMAINS = ("meridian.example", "globex.example")
EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)")
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@dataclass
class Decision:
    effect: str  # allow | deny | approval_required
    reason: str
    risk: str = "low"
    is_write: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def __iter__(self):  # backwards compatible with the original `(decision, reason)` tuple
        yield self.effect
        yield self.reason


def _path_of(args: dict) -> str:
    return str(args.get("entityUrl") or args.get("entityUrls") or args.get("path") or "")


def _external_recipients(args: dict) -> list[str]:
    found = EMAIL_RE.finditer(str(args.get("to", "")) + " " + str(args.get("body", {}) if isinstance(args.get("body"), dict) else ""))
    return sorted({m.group(0) for m in found if not m.group(1).lower().endswith(INTERNAL_DOMAINS)})


class PolicyEngine:
    def classify(self, server: str, tool: str, args: dict, meta: dict | None) -> tuple[bool, str, str]:
        """Return (is_write, risk, rationale) for a tool call."""
        meta = meta or {}
        if server == "workiq" or (not meta and tool in READ_TOOLS | WRITE_TOOLS):
            is_write = tool in WRITE_TOOLS
        else:
            # MCP annotations are hints from the server; anything not explicitly read-only is a write.
            is_write = not meta.get("read_only", False)
        if not is_write:
            return False, "low", "read-only tool"
        risk, why = "medium", "state-changing tool"
        path = _path_of(args).lower()
        if meta.get("destructive") or tool == "delete_entity":
            risk, why = "high", "destructive operation"
        if "sendmail" in path or tool in {"send_email", "escalate_ticket", "post_message"} or meta.get("external"):
            risk, why = "high", "communication or escalation leaves the workspace boundary"
        if _external_recipients(args):
            risk, why = "high", "external recipients: " + ", ".join(_external_recipients(args))
        return True, risk, why

    def decide(self, p, tool: str, args: dict, approved: bool = False, *, server: str = "workiq",
               meta: dict | None = None, tool_enabled: bool = True) -> Decision:
        path = _path_of(args)
        if any(x.lower() in path.lower() for x in BLOCKED_SEGMENTS):
            return Decision("deny", "blocked resource segment", "critical")
        if not tool_enabled:
            return Decision("deny", "tool disabled by workspace administrator", "medium")
        known = bool(meta) or tool in READ_TOOLS | WRITE_TOOLS
        if not known:
            return Decision("deny", "unknown capability", "critical")
        is_write, risk, why = self.classify(server, tool, args, meta)
        if not is_write:
            if "workiq:read" not in p.scopes:
                return Decision("deny", "missing read scope", risk)
            return Decision("allow", "read allowed", risk)
        if "workiq:write" not in p.scopes:
            return Decision("deny", "missing write scope", risk, True)
        leaks = find_sensitive(str(args))
        if leaks:
            return Decision("deny", "sensitive data detected in action payload: " + ", ".join(leaks), "critical", True)
        if not approved:
            return Decision("approval_required", f"mutation requires explicit approval ({why})", risk, True)
        return Decision("allow", f"approved mutation ({why})", risk, True)


policy = PolicyEngine()
