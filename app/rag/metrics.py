"""Retrieval and grounding metrics (deterministic, reference-based)."""
from __future__ import annotations

import math
import re

from app.rag.embeddings import tokenize


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    return len(set(ranked[:k]) & relevant) / len(relevant) if relevant else 0.0


def mrr(ranked: list[str], relevant: set[str]) -> float:
    for i, x in enumerate(ranked):
        if x in relevant:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    seen: set[str] = set()
    dcg = 0.0
    for i, x in enumerate(ranked[:k]):
        if x in relevant and x not in seen:  # each relevant item only earns gain once
            dcg += 1.0 / math.log2(i + 2)
            seen.add(x)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / ideal if ideal else 0.0


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\[])", text) if len(s.strip()) > 20]


def support(claim: str, evidence: str) -> float:
    """Share of the claim's content tokens that appear in the evidence (lexical entailment proxy)."""
    c = set(tokenize(re.sub(r"\[\d+\]", "", claim)))
    if not c:
        return 1.0
    return len(c & set(tokenize(evidence))) / len(c)


def citation_correctness(claims: list[dict], threshold: float = 0.6) -> tuple[float, list[dict]]:
    """claims: [{text, evidence:[str]}]. A cited claim is correct when a cited passage supports it."""
    details = []
    for c in claims:
        best = max((support(c["text"], e) for e in c["evidence"]), default=0.0)
        details.append({"text": c["text"][:160], "support": round(best, 3), "ok": best >= threshold})
    return (sum(d["ok"] for d in details) / len(details) if details else 1.0), details


def faithfulness(answer_sentences: list[str], context: list[str], threshold: float = 0.6) -> float:
    if not answer_sentences:
        return 1.0
    joined = "\n".join(context)
    return sum(1 for s in answer_sentences if support(s, joined) >= threshold) / len(answer_sentences)
