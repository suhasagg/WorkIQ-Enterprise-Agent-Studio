"""The original /v1 surface, kept working on top of the new runtime."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import ctx_for
from app.audit import audit
from app.graph.runner import runner
from app.memory import memory
from app.security import Principal, principal, require
from app.workiq.discovery import discovery

router = APIRouter(prefix="/v1", tags=["v1 (original API)"])


class WorkplaceRequest(BaseModel):
    request: str = Field(..., min_length=1, max_length=4000)


@router.post("/run")
async def run(r: WorkplaceRequest, approved: bool = Query(False), p: Principal = Depends(require("agent:run"))):
    """Runs the request through the LangGraph graph and waits. Mutations are never executed from a query flag any more:
    `approved=true` is ignored and the run stops at `waiting_approval` until decided in the Approval Center."""
    out = await runner.start(p, r.request, wait=True)
    return {"run_id": out["id"], "status": out["status"], "intent": out.get("intent"), "result": out.get("result"),
            "error": out.get("error"), "approved_flag_ignored": approved}


@router.get("/discover")
async def discover(q: str, p: Principal = Depends(principal)):
    return await discovery.paths(q, ctx_for(p))


@router.get("/schema")
async def schema(path: str, operation: str = "fetch", p: Principal = Depends(principal)):
    return await discovery.schema(path, operation, ctx_for(p))


@router.get("/memory")
async def mem(p: Principal = Depends(principal)):
    return await memory.snapshot(p.tenant)


@router.get("/audit")
async def aud(p: Principal = Depends(require("audit:read"))):
    return await audit.list(p.tenant)
