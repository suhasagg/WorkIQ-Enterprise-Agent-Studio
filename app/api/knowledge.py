from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app import db
from app.api.deps import not_found
from app.audit import audit
from app.config import settings
from app.rag.embeddings import embedder
from app.rag.ingest import create_document, ingest_document
from app.rag.retrieve import search
from app.security import ROLE_SCOPES, Principal, principal, require

router = APIRouter(prefix="/api", tags=["knowledge"])
_tasks: set[asyncio.Task] = set()
DOC_COLS = """id, title, filename, mime, source_type, size_bytes, status, stage, progress, error, chunk_strategy, chunk_count,
              metadata, allowed_roles, untrusted_flags, embedding_model, origin, created_by, created_at, indexed_at"""


def _visible(p: Principal) -> tuple[str, list]:
    return "workspace_id=%s AND (cardinality(allowed_roles)=0 OR %s = ANY(allowed_roles))", [p.tenant, p.role]


def _store(doc_id: str, filename: str) -> Path:
    d = Path(settings.upload_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{doc_id}{Path(filename).suffix.lower()}"


def _spawn(coro) -> None:
    t = asyncio.get_running_loop().create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


@router.get("/documents")
async def documents(p: Principal = Depends(principal)):
    w, params = _visible(p)
    return await db.fetch_all(f"SELECT {DOC_COLS} FROM documents WHERE {w} ORDER BY created_at DESC, title", params)


@router.post("/documents", status_code=201)
async def upload(file: UploadFile = File(...), title: str | None = Form(None), allowed_roles: str = Form(""),
                 chunk_strategy: str = Form("auto"), tags: str = Form(""), p: Principal = Depends(require("knowledge:write"))):
    if chunk_strategy not in ("auto", "heading", "window"):
        raise HTTPException(400, "chunk_strategy must be auto, heading or window")
    roles = [r.strip() for r in allowed_roles.split(",") if r.strip()]
    if any(r not in ROLE_SCOPES for r in roles):
        raise HTTPException(400, "unknown role in allowed_roles")
    if roles and p.role not in roles:
        roles.append(p.role)  # the uploader keeps access to their own document
    data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"file exceeds {settings.max_upload_mb} MB")
    if not data:
        raise HTTPException(400, "file is empty")
    name = Path(file.filename or "upload").name
    try:
        doc, created = await create_document(workspace_id=p.tenant, filename=name, data=data, created_by=p.subject, title=title or None,
                                             allowed_roles=roles, chunk_strategy=chunk_strategy,
                                             tags=[t.strip() for t in tags.split(",") if t.strip()])
    except ValueError as e:
        raise HTTPException(415, str(e)) from None
    if created:
        _store(doc["id"], name).write_bytes(data)
        _spawn(ingest_document(doc["id"], data, delay=0.35 if settings.demo_mode else 0))
        await audit.append({"workspace_id": p.tenant, "actor": p.subject, "action": "document.upload", "resource": doc["id"],
                            "decision": "allow", "detail": {"filename": name, "bytes": len(data), "allowed_roles": roles}})
    return {**{k: doc[k] for k in doc if k != "sha256"}, "duplicate": not created}


async def _doc(doc_id: str, p: Principal) -> dict:
    w, params = _visible(p)
    doc = await db.fetch_one(f"SELECT {DOC_COLS} FROM documents WHERE id=%s AND {w}", [doc_id, *params])
    if not doc:
        raise not_found("document")
    return doc


@router.get("/documents/{doc_id}")
async def document(doc_id: str, p: Principal = Depends(principal)):
    doc = await _doc(doc_id, p)
    doc["chunks"] = await db.fetch_all(
        "SELECT id, ordinal, heading, page, content, token_count, flags FROM chunks WHERE document_id=%s ORDER BY ordinal", (doc_id,))
    return doc


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, p: Principal = Depends(require("knowledge:write"))):
    doc = await _doc(doc_id, p)
    await db.execute("DELETE FROM documents WHERE id=%s", (doc_id,))
    _store(doc_id, doc["filename"]).unlink(missing_ok=True)
    await audit.append({"workspace_id": p.tenant, "actor": p.subject, "action": "document.delete", "resource": doc_id, "decision": "allow"})


@router.post("/documents/{doc_id}/reindex")
async def reindex(doc_id: str, p: Principal = Depends(require("knowledge:write"))):
    doc = await _doc(doc_id, p)
    path = _store(doc_id, doc["filename"])
    if not path.exists():
        raise HTTPException(409, "the original file is not stored on this server (seeded demo documents are re-created by the seeder)")
    await db.execute("UPDATE documents SET status='queued', stage='queued', progress=0, error=NULL WHERE id=%s", (doc_id,))
    _spawn(ingest_document(doc_id, path.read_bytes()))
    return {"ok": True}


class Search(BaseModel):
    query: str = Field(..., min_length=2, max_length=500)
    k: int = Field(6, ge=1, le=20)
    mode: str = Field("hybrid", pattern="^(hybrid|vector|keyword)$")
    rerank: bool = True
    source_type: str | None = None
    tag: str | None = None
    document_id: str | None = None


@router.post("/knowledge/search")
async def knowledge_search(body: Search, p: Principal = Depends(principal)):
    filters = {k: v for k, v in {"source_type": body.source_type, "tag": body.tag, "document_id": body.document_id}.items() if v}
    return await search(p, body.query, k=body.k, mode=body.mode, filters=filters, rerank=body.rerank)


@router.get("/knowledge/stats")
async def knowledge_stats(p: Principal = Depends(principal)):
    w, params = _visible(p)
    s = await db.fetch_one(
        f"""SELECT count(*) AS documents, COALESCE(sum(chunk_count),0)::int AS chunks,
                   count(*) FILTER (WHERE status='indexed') AS indexed, count(*) FILTER (WHERE status='failed') AS failed,
                   count(*) FILTER (WHERE status IN ('queued','processing')) AS processing,
                   count(*) FILTER (WHERE cardinality(allowed_roles)>0) AS restricted,
                   count(*) FILTER (WHERE jsonb_array_length(untrusted_flags)>0) AS flagged
            FROM documents WHERE {w}""", params)
    return {**s, "embedding_model": embedder.model_name, "embedding_dim": settings.embedding_dim,
            "vector_store": "PostgreSQL + pgvector (HNSW, cosine)", "keyword_index": "PostgreSQL full-text (GIN)"}
