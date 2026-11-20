"""Hybrid retrieval: pgvector cosine + PostgreSQL full-text, fused with RRF, then reranked.

Access control is enforced in SQL: a chunk is only visible inside its own workspace and, when the document
lists `allowed_roles`, only to principals holding one of those roles.
"""
from __future__ import annotations

import re
import time

from app import db
from app.rag.embeddings import cosine, embedder, hash_embed, tokenize, vec_literal
from app.security import Principal

RRF_K = 60


def _or_query(q: str) -> str:
    terms = [t for t in dict.fromkeys(re.findall(r"[a-zA-Z0-9]{3,}", q.lower()))][:24]
    return " | ".join(terms)


def highlight_terms(query: str, text: str) -> list[str]:
    q = set(tokenize(query))
    return sorted({w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]+", text) if tokenize(w) and tokenize(w)[0] in q})[:20]


def rerank_score(query: str, heading: str | None, content: str) -> float:
    """Deterministic reranker: query-term coverage + heading match + lexical-embedding similarity."""
    q = set(tokenize(query))
    if not q:
        return 0.0
    c = tokenize(content)
    cset = set(c) | set(tokenize(heading or ""))
    coverage = len(q & cset) / len(q)
    head = len(q & set(tokenize(heading or ""))) / len(q)
    density = sum(1 for t in c if t in q) / (len(c) + 20)
    sim = cosine(hash_embed(query), hash_embed((heading or "") + "\n" + content))
    return round(0.45 * coverage + 0.25 * head + 0.15 * min(density * 8, 1.0) + 0.15 * max(sim, 0.0), 6)


async def search(p: Principal, query: str, *, k: int = 6, mode: str = "hybrid", filters: dict | None = None,
                 rerank: bool = True, candidates: int = 24) -> dict:
    t0 = time.perf_counter()
    filters = filters or {}
    where = ["c.workspace_id = %(ws)s", "d.status = 'indexed'",
             "(cardinality(d.allowed_roles) = 0 OR %(role)s = ANY(d.allowed_roles))"]
    params: dict = {"ws": p.tenant, "role": p.role, "n": candidates}
    if filters.get("source_type"):
        where.append("d.source_type = %(st)s")
        params["st"] = filters["source_type"]
    if filters.get("tag"):
        where.append("d.metadata->'tags' ? %(tag)s")
        params["tag"] = filters["tag"]
    if filters.get("document_id"):
        where.append("d.id = %(doc)s")
        params["doc"] = filters["document_id"]
    w = " AND ".join(where)
    cols = "c.id, c.document_id, c.ordinal, c.heading, c.page, c.content, c.flags, d.title, d.filename, d.source_type, d.metadata"
    ranked: dict[str, dict] = {}
    if mode in ("hybrid", "vector"):
        params["qv"] = vec_literal(await embedder.embed_one(query))
        rows = await db.fetch_all(
            f"""SELECT {cols}, 1 - (c.embedding <=> %(qv)s::vector) AS vscore FROM chunks c JOIN documents d ON d.id=c.document_id
                WHERE {w} ORDER BY c.embedding <=> %(qv)s::vector LIMIT %(n)s""", params)
        for rank, r in enumerate(rows):
            ranked[r["id"]] = {**r, "vector_rank": rank + 1, "vector_score": round(float(r.pop("vscore")), 4)}
    if mode in ("hybrid", "keyword"):
        params["tsq"] = _or_query(query)
        if params["tsq"]:
            rows = await db.fetch_all(
                f"""SELECT {cols}, ts_rank_cd(c.tsv, to_tsquery('english', %(tsq)s)) AS kscore FROM chunks c
                    JOIN documents d ON d.id=c.document_id WHERE {w} AND c.tsv @@ to_tsquery('english', %(tsq)s)
                    ORDER BY kscore DESC LIMIT %(n)s""", params)
            for rank, r in enumerate(rows):
                k_score = round(float(r.pop("kscore")), 4)
                ranked.setdefault(r["id"], dict(r)).update({"keyword_rank": rank + 1, "keyword_score": k_score})
    results = []
    top_rrf = 2 / (RRF_K + 1)
    for r in ranked.values():
        r["rrf"] = round(sum(1 / (RRF_K + r[x]) for x in ("vector_rank", "keyword_rank") if r.get(x)), 6)
        r["rerank_score"] = rerank_score(query, f"{r['title']} {r['heading'] or ''}", r["content"]) if rerank else None
        r["score"] = round(0.8 * r["rerank_score"] + 0.2 * r["rrf"] / top_rrf, 6) if rerank else r["rrf"]
        r["highlights"] = highlight_terms(query, r["content"])
        r["untrusted"] = bool(r["flags"])
        r["tags"] = (r.pop("metadata") or {}).get("tags", [])
        results.append(r)
    results.sort(key=lambda r: (-r["score"], r["id"]))
    return {"query": query, "mode": mode, "reranked": rerank, "results": results[:k], "candidates": len(results),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "embedding_model": embedder.model_name}
