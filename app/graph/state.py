"""Typed shared state for the LangGraph runtime."""
from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict


def merge_by_id(left: list[dict] | None, right: list[dict] | None) -> list[dict]:
    out = {x["id"]: x for x in (left or [])}
    for x in right or []:
        out[x["id"]] = {**out.get(x["id"], {}), **x}
    return list(out.values())


class Step(TypedDict, total=False):
    id: str
    agent: str          # research | analytics | task | communication
    title: str
    mode: str
    params: dict
    status: str         # pending | running | completed | failed | skipped
    attempts: int
    error: str | None
    summary: str | None
    optional: bool


class AgentState(TypedDict, total=False):
    run_id: str
    request: str
    workflow: str
    intent: str
    plan: list[Step]
    cursor: str | None
    step_result: dict | None
    route: str
    replans: int
    evidence: Annotated[list[dict], merge_by_id]
    findings: Annotated[list[dict], merge_by_id]
    analytics: dict
    extracted: dict
    workplace: dict
    scratch: dict
    proposed_actions: Annotated[list[dict], merge_by_id]
    action_results: Annotated[list[dict], merge_by_id]
    approval_round: int
    guardrails: Annotated[list[dict], add]
    errors: Annotated[list[dict], add]
    draft: dict
    evaluation: dict
    revisions: int
    final: dict[str, Any]


def current_step(state: AgentState) -> Step:
    for s in state.get("plan") or []:
        if s["id"] == state.get("cursor"):
            return s
    return {"id": "adhoc", "agent": "", "mode": "", "params": {}}
