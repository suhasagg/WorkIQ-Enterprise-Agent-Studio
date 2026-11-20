from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app import db
from app.api.deps import not_found
from app.evals import runner as evals
from app.security import Principal, principal, require

router = APIRouter(prefix="/api/evals", tags=["evals"])


class EvalConfig(BaseModel):
    mode: str = Field("hybrid", pattern="^(hybrid|vector|keyword)$")
    rerank: bool = True


class EvalStart(BaseModel):
    dataset: str
    config: EvalConfig = EvalConfig()


@router.get("/datasets")
async def datasets(p: Principal = Depends(principal)):
    out = []
    for d in evals.datasets():
        last = await db.fetch_one(
            "SELECT id, status, metrics, passed, total, created_at FROM eval_runs WHERE workspace_id=%s AND dataset=%s ORDER BY created_at DESC LIMIT 1",
            (p.tenant, d["id"]))
        base = await db.fetch_one(
            "SELECT id, metrics, created_at FROM eval_runs WHERE workspace_id=%s AND dataset=%s AND is_baseline ORDER BY created_at DESC LIMIT 1",
            (p.tenant, d["id"]))
        out.append({"id": d["id"], "name": d["name"], "kind": d["kind"], "description": d["description"], "cases": len(d["cases"]),
                    "thresholds": d.get("thresholds", {}), "last_run": last, "baseline": base})
    return out


@router.get("/datasets/{dataset_id}")
async def dataset(dataset_id: str, p: Principal = Depends(principal)):
    d = evals.dataset(dataset_id)
    if not d:
        raise not_found("dataset")
    return d


@router.post("/runs", status_code=201)
async def start(body: EvalStart, p: Principal = Depends(require("eval:run"))):
    if p.tenant != "meridian":
        raise HTTPException(400, "the bundled datasets are labelled against the Meridian demo corpus")
    try:
        return await evals.start(p, body.dataset, body.config.model_dump())
    except LookupError:
        raise not_found("dataset") from None


@router.get("/runs")
async def runs(p: Principal = Depends(principal), dataset: str | None = None, limit: int = Query(40, ge=1, le=200)):
    where, params = ["workspace_id=%s"], [p.tenant]
    if dataset:
        where.append("dataset=%s")
        params.append(dataset)
    return await db.fetch_all(
        f"""SELECT id, dataset, status, metrics, total, passed, is_baseline, origin, error, config, created_by, created_at, finished_at
            FROM eval_runs WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT %s""", (*params, limit))


@router.get("/runs/{eval_run_id}")
async def run(eval_run_id: str, p: Principal = Depends(principal)):
    r = await evals.get(eval_run_id, p.tenant)
    if not r:
        raise not_found("evaluation run")
    return r


@router.post("/runs/{eval_run_id}/baseline")
async def baseline(eval_run_id: str, p: Principal = Depends(require("eval:run"))):
    try:
        await evals.set_baseline(eval_run_id, p.tenant)
    except LookupError:
        raise not_found("completed evaluation run") from None
    return {"ok": True}
