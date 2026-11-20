"""Shared API helpers."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.config import settings
from app.context import RunCtx
from app.security import Principal

_hits: dict[str, deque] = defaultdict(deque)


def ctx_for(p: Principal, **kw) -> RunCtx:
    return RunCtx(principal=p, **kw)


def not_found(what: str = "resource") -> HTTPException:
    return HTTPException(404, f"{what} not found")


async def rate_limit(request: Request) -> None:
    """Fixed-window in-process limiter per client. Use a shared store behind multiple replicas."""
    key = (request.headers.get("authorization") or request.cookies.get("wiq_session") or request.client.host or "anon")[-48:]
    now, q = time.monotonic(), _hits[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= settings.rate_limit_per_minute:
        raise HTTPException(429, "rate limit exceeded; retry shortly", headers={"Retry-After": "10"})
    q.append(now)
