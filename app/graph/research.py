"""Research agent — RAG subgraph: plan_queries -> retrieve -> grade -> extract."""
from __future__ import annotations

import hashlib

from app.context import RunCtx
from app.graph.runtime import StepError, linear_subgraph, node
from app.graph.state import AgentState, current_step
from app.llm.model import estimate_tokens, record
from app.rag.embeddings import tokenize
from app.rag.ingest import scan_untrusted
from app.rag.metrics import split_sentences
from app.rag.retrieve import search

AGENT = "research"
MIN_SCORE = 0.22


def _queries_for(state: AgentState, step: dict) -> list[dict]:
    mode = step.get("mode")
    qs: list[dict] = []
    if mode == "anomaly_context":
        for a in (state.get("analytics") or {}).get("anomalies", [])[:5]:
            qs.append({"q": f"{a['label']} {a['description']} causes drivers", "topic": a["label"], "ref": a["metric"]})
        qs.append({"q": "major operational risks and mitigation", "topic": "Operational risks", "ref": "risks"})
    elif mode == "ticket_context":
        for c in (state.get("analytics") or {}).get("clusters", [])[:3]:
            qs.append({"q": f"{c['label']} symptom cause workaround permanent fix", "topic": c["label"], "ref": c["category"]})
        qs.append({"q": "priority-one SLA breach escalation tier three policy", "topic": "Escalation policy", "ref": "policy"})
        qs.append({"q": "customer communication status update during priority-one incident", "topic": "Customer communication", "ref": "comms"})
    elif mode == "meeting_context":
        for d in (state.get("extracted") or {}).get("decisions", [])[:3]:
            qs.append({"q": d["text"], "topic": d["text"][:60], "ref": d["id"]})
    else:
        for q in step.get("params", {}).get("queries") or [state["request"]]:
            qs.append({"q": q, "topic": q[:80], "ref": "question"})
    return qs


@node("research.plan_queries", AGENT, step=True)
async def plan_queries(state: AgentState, ctx: RunCtx) -> dict:
    step = current_step(state)
    qs = _queries_for(state, step)
    if not qs:
        return {"scratch": {"queries": [], "hits": []}, "step_result": {"status": "completed", "summary": "No retrieval needed"}}
    await record(ctx, "query_planning", "deterministic", "deterministic-demo-v1", estimate_tokens(state["request"]),
                 sum(estimate_tokens(q["q"]) for q in qs), 0, True)
    ctx.emit({"type": "queries_planned", "queries": [q["q"] for q in qs]})
    return {"scratch": {"queries": qs, "hits": []}, "step_result": None}


@node("research.retrieve", AGENT, step=True)
async def retrieve(state: AgentState, ctx: RunCtx) -> dict:
    step = current_step(state)
    opts = {**ctx.options, **step.get("params", {})}
    mode = opts.get("retrieval_mode", "hybrid")
    fault = ctx.options.get("fault_injection") or {}
    if fault.get("node") == "research.retrieve" and mode == "hybrid" and step.get("attempts", 0) < int(fault.get("times", 1)):
        raise StepError("simulated retrieval outage (fault injection enabled for this run)")
    hits = []
    for q in state["scratch"]["queries"]:
        res = await search(ctx.principal, q["q"], k=int(opts.get("k", 4)), mode=mode, filters=opts.get("filters"))
        ctx.emit({"type": "retrieval", "query": q["q"], "mode": res["mode"], "candidates": res["candidates"],
                  "latency_ms": res["latency_ms"], "embedding_model": res["embedding_model"],
                  "results": [{"chunk_id": r["id"], "title": r["title"], "heading": r["heading"], "score": r["score"]}
                              for r in res["results"]]})
        hits.append({**q, "results": [{k: r.get(k) for k in ("id", "document_id", "title", "filename", "heading", "page",
                                                              "content", "score", "untrusted", "flags", "highlights")}
                                      for r in res["results"]]})
    return {"scratch": {**state["scratch"], "hits": hits}}


# Words that carry no topical meaning in a question and must not count against a passage.
QUESTION_WORDS = set(tokenize(
    "what when why how which who whom whose where should must may recommend tell show find about them they their it its us "
    "we our major main key biggest top current latest most many much any some"))
MIN_COVERAGE = 0.5


def _same_term(a: str, b: str) -> bool:
    """Light morphological match so 'own' covers 'owner' and 'escalat' covers 'escalation'."""
    return a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))


def coverage(query: str, r: dict) -> float:
    """Share of the question's content terms that occur in the passage (with its title and heading)."""
    q = set(tokenize(query)) - QUESTION_WORDS
    if not q:
        return 1.0
    passage = set(tokenize(f"{r['title']} {r.get('heading') or ''} {r['content']}"))
    return sum(any(_same_term(t, w) for w in passage) for t in q) / len(q)


@node("research.grade", AGENT, step=True)
async def grade(state: AgentState, ctx: RunCtx) -> dict:
    """Relevance grading plus prompt-injection defence: instructions inside documents are data, never commands."""
    graded, guardrails, dropped = [], [], 0
    for h in state["scratch"]["hits"]:
        keep = []
        for r in h["results"]:
            if r["score"] < MIN_SCORE or (h["ref"] == "question" and coverage(h["q"], r) < MIN_COVERAGE):
                dropped += 1  # off-topic passages are dropped so the agent abstains instead of guessing
                continue
            sentences = split_sentences(r["content"]) or [r["content"]]
            safe = [s for s in sentences if not scan_untrusted(s)]
            informative = [s for s in safe if len(tokenize(s)) >= 6]  # drop boilerplate fragments
            if len(safe) != len(sentences) or r.get("untrusted"):
                guardrails.append({"type": "prompt_injection_in_document", "action": "instructions quarantined",
                                   "document": r["title"], "chunk_id": r["id"], "flags": r.get("flags") or scan_untrusted(r["content"])})
            if informative:
                keep.append({**r, "sentences": informative})
        graded.append({**h, "results": keep})
    for g in guardrails:
        ctx.emit({**g, "type": "guardrail", "kind": g["type"]})
    ctx.emit({"type": "grading", "kept": sum(len(h["results"]) for h in graded), "dropped": dropped})
    return {"scratch": {**state["scratch"], "hits": graded}, "guardrails": guardrails}


def best_sentences(query: str, sentences: list[str], n: int = 2) -> list[str]:
    q = set(tokenize(query))
    scored = sorted(((len(q & set(tokenize(s))) / (len(q) or 1), -i, s) for i, s in enumerate(sentences)), reverse=True)
    picked = [s for sc, _, s in scored[:n] if sc > 0] or sentences[:1]
    return [s for s in sentences if s in picked]  # keep document order


@node("research.extract", AGENT, step=True)
async def extract(state: AgentState, ctx: RunCtx) -> dict:
    evidence, findings = [], []
    seen = {e["chunk_id"] for e in state.get("evidence") or []}
    for h in state["scratch"]["hits"]:
        top = h["results"][0]["score"] if h["results"] else 0
        rel, limit = (0.6, 5) if h["ref"] == "question" else (0.75, 3)
        for rank, r in enumerate([r for r in h["results"] if r["score"] >= rel * top][:limit]):
            eid = "E" + hashlib.sha256(r["id"].encode()).hexdigest()[:8]
            sents = best_sentences(h["q"], r["sentences"], 2 if rank == 0 else 1)
            if r["id"] not in seen:
                seen.add(r["id"])
                evidence.append({"id": eid, "chunk_id": r["id"], "document_id": r["document_id"], "title": r["title"],
                                 "filename": r["filename"], "heading": r["heading"], "page": r["page"], "score": r["score"],
                                 "snippet": " ".join(r["sentences"])[:700], "highlights": r["highlights"],
                                 "untrusted": bool(r.get("untrusted")), "query": h["q"]})
            findings.append({"id": f"F-{h['ref']}-{eid}", "topic": h["topic"], "ref": h["ref"], "text": " ".join(sents),
                             "citations": [eid], "score": r["score"]})
    n_docs = len({e["document_id"] for e in evidence})
    summary = f"{len(findings)} findings from {len(evidence)} passages in {n_docs} documents"
    if not findings:
        summary = "No sufficiently relevant passages were found in the knowledge base"
    return {"evidence": evidence, "findings": findings, "step_result": {"status": "completed", "summary": summary}}


def build():
    return linear_subgraph(
        [("plan_queries", plan_queries), ("retrieve", retrieve), ("grade", grade), ("extract", extract)],
        branches={"plan_queries": (lambda s: "retrieve" if s["scratch"]["queries"] else "__end__", {"retrieve": "retrieve"})},
    ).compile()
