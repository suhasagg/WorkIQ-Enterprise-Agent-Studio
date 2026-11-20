"""Sensitive-data detection and redaction used for logs, traces, audit records and DLP checks."""
from __future__ import annotations

import re
from typing import Any

PATTERNS: dict[str, re.Pattern[str]] = {
    "api_key": re.compile(r"\b(?:sk|pk|ghp|gho|xox[abp])[-_][A-Za-z0-9_\-]{16,}\b"),
    "bearer_token": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"),
    "password_assignment": re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S{6,}"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
SENSITIVE_KEYS = {"password", "secret", "token", "authorization", "api_key", "apikey", "credential", "access_token"}


def find_sensitive(text: str) -> list[str]:
    return [name for name, pat in PATTERNS.items() if pat.search(text or "")]


def redact_text(text: str) -> str:
    out = text
    for name, pat in PATTERNS.items():
        out = pat.sub(f"[REDACTED:{name}]", out)
    return out


def redact(value: Any, max_len: int = 2000) -> Any:
    """Recursively redact secrets; long strings are truncated so traces stay bounded."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if str(k).lower() in SENSITIVE_KEYS else redact(v, max_len)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v, max_len) for v in value[:50]]
    if isinstance(value, str):
        s = redact_text(value)
        return s if len(s) <= max_len else s[:max_len] + f"… [{len(s) - max_len} chars truncated]"
    return value
