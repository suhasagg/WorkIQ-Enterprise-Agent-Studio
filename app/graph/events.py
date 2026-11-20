"""Run event log: every graph/tool/approval event is persisted to Postgres in order and fanned out to SSE subscribers."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from app import db
from app.redaction import redact


class EventBus:
    def __init__(self) -> None:
        self._pending: set[asyncio.Task] = set()
        self._lock: asyncio.Lock | None = None
        self._cond: asyncio.Condition | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _bind(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop, self._lock, self._cond, self._pending = loop, asyncio.Lock(), asyncio.Condition(), set()

    async def write(self, run_id: str, workspace_id: str, event: dict, node: str | None = None, agent: str | None = None) -> None:
        self._bind()
        payload = redact({k: v for k, v in event.items() if k != "type"}, 4000)
        async with self._lock:  # FIFO lock keeps sequence order equal to emission order
            await db.execute(
                "INSERT INTO run_events(run_id, workspace_id, type, node, agent, payload) VALUES (%s,%s,%s,%s,%s,%s)",
                (run_id, workspace_id, event["type"], node, agent, db.J(payload)))
        async with self._cond:
            self._cond.notify_all()

    def emit(self, run_id: str, workspace_id: str, event: dict, node: str | None = None, agent: str | None = None) -> None:
        """Synchronous, non-blocking emit usable from any coroutine."""
        self._bind()
        task = asyncio.get_running_loop().create_task(self.write(run_id, workspace_id, event, node, agent))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def emitter(self, run_id: str, workspace_id: str, node: str | None = None, agent: str | None = None) -> Callable[[dict], None]:
        return lambda event: self.emit(run_id, workspace_id, event, node, agent)

    async def flush(self) -> None:
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def stream(self, run_id: str, workspace_id: str, after: int = 0, heartbeat: float = 15.0) -> AsyncIterator[dict | None]:
        """Yield persisted events with seq > after; yields None as a heartbeat. Ends after a terminal run event."""
        self._bind()
        terminal = {"run_completed", "run_failed", "run_cancelled"}
        while True:
            rows = await db.fetch_all(
                "SELECT seq, ts, type, node, agent, payload FROM run_events WHERE run_id=%s AND workspace_id=%s AND seq>%s ORDER BY seq",
                (run_id, workspace_id, after))
            for r in rows:
                after = r["seq"]
                yield r
                if r["type"] in terminal:
                    return
            if not rows:
                try:
                    async with self._cond:
                        await asyncio.wait_for(self._cond.wait(), timeout=min(heartbeat, 2.0))
                except TimeoutError:
                    yield None


bus = EventBus()
