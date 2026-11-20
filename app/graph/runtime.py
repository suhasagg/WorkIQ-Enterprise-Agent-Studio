"""Node plumbing shared by every graph node: run context, timing, events, timeouts and step-failure capture."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, StateGraph

from app.config import settings
from app.context import RunCtx
from app.graph.events import bus
from app.graph.state import AgentState
from app.metrics import NODE_LATENCY
from app.security import Principal
from app.telemetry import span

log = structlog.get_logger()


class StepError(Exception):
    """A recoverable failure of the current plan step (the supervisor decides whether to retry or replan)."""


def make_ctx(config: RunnableConfig, node: str | None = None, agent: str | None = None) -> RunCtx:
    c = config.get("configurable", {})
    p = Principal.from_dict(c["principal"])
    run_id = c.get("run_id")
    emit = bus.emitter(run_id, p.tenant, node, agent) if run_id else (lambda e: None)
    return RunCtx(principal=p, run_id=run_id, node=node, agent=agent, emit=emit, options=c.get("options") or {},
                  origin=c.get("origin", "live"))


def node(name: str, agent: str, *, step: bool = False) -> Callable:
    """Wrap a node coroutine `fn(state, ctx) -> dict`.

    Emits node_start/node_end, enforces the node timeout and records latency. With step=True, failures are captured
    into `step_result` (so the supervisor can retry/replan) instead of failing the whole run.
    """
    def deco(fn: Callable[[AgentState, RunCtx], Awaitable[dict]]):
        async def wrapper(state: AgentState, config: RunnableConfig) -> dict:
            ctx = make_ctx(config, name, agent)
            t0 = time.perf_counter()
            ctx.emit({"type": "node_start"})
            status, error, out = "ok", None, {}
            try:
                with span(f"graph.{name}", **{"run.id": ctx.run_id, "agent": agent}):
                    out = await asyncio.wait_for(fn(state, ctx), timeout=settings.node_timeout_seconds) or {}
            except GraphBubbleUp:  # interrupt(): the run is pausing here, not failing
                ctx.emit({"type": "node_end", "status": "interrupted", "duration_ms": int((time.perf_counter() - t0) * 1000)})
                raise
            except asyncio.CancelledError:
                raise
            except Exception as e:
                status = "error"
                error = f"node timed out after {settings.node_timeout_seconds}s" if isinstance(e, TimeoutError) else str(e)
                if not step:
                    ctx.emit({"type": "node_end", "status": status, "error": error,
                              "duration_ms": int((time.perf_counter() - t0) * 1000)})
                    raise
                if not isinstance(e, StepError):
                    log.exception("step_failed", node=name)
                out = {"step_result": {"status": "failed", "error": error, "node": name},
                       "errors": [{"node": name, "agent": agent, "error": error}]}
            dur = time.perf_counter() - t0
            NODE_LATENCY.labels(name).observe(dur)
            ctx.emit({"type": "node_end", "status": status, "error": error, "duration_ms": int(dur * 1000),
                      "summary": (out.get("step_result") or {}).get("summary") if isinstance(out, dict) else None})
            return out
        wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
        wrapper.node_name, wrapper.agent = name, agent  # type: ignore[attr-defined]
        return wrapper
    return deco


def failed(state: AgentState) -> bool:
    return (state.get("step_result") or {}).get("status") == "failed"


def linear_subgraph(nodes: list[tuple[str, Callable]], branches: dict[str, tuple[Callable, dict]] | None = None) -> StateGraph:
    """Build a subgraph that runs nodes in order and short-circuits to END when a node reports a failed step.

    `branches` maps a node name to (router, mapping) for conditional fan-out after that node.
    """
    g = StateGraph(AgentState)
    for name, fn in nodes:
        g.add_node(name, fn)
    g.set_entry_point(nodes[0][0])
    branches = branches or {}
    names = [n for n, _ in nodes]
    for i, name in enumerate(names):
        if name in branches:
            router, mapping = branches[name]
            g.add_conditional_edges(name, lambda s, r=router: END if failed(s) else r(s), {**mapping, END: END})
        elif getattr(dict(nodes)[name], "terminal", False) or i == len(names) - 1:
            g.add_edge(name, END)
        else:
            nxt = names[i + 1]
            g.add_conditional_edges(name, lambda s, n=nxt: END if failed(s) else n, {nxt: nxt, END: END})
    return g
