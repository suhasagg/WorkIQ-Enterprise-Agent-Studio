"""Embedding providers.

Default: `local-hash-384`, a deterministic signed feature-hashing embedding over word unigrams, bigrams and
character trigrams. It is a lexical embedding (not a neural model) that needs no network or paid account,
which keeps the demo and CI reproducible. Set EMBEDDING_PROVIDER=openai (+ OPENAI_API_KEY) for neural embeddings.
"""
from __future__ import annotations

import hashlib
import math
import re

from app.config import settings

DIM = settings.embedding_dim
TOKEN_RE = re.compile(r"[a-z0-9]+")
STOP = set("the a an and or of to in for on at by with from is are was were be been this that it as we our their its "
           "will can has have had not but if then than so do does did into over per via".split())


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in TOKEN_RE.findall(text.lower()) if t not in STOP and len(t) > 1]


def _stem(t: str) -> str:
    for suf in ("ization", "ations", "ation", "ments", "ment", "ings", "ing", "ies", "ed", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            return t[: -len(suf)] + ("y" if suf == "ies" else "")
    return t


def _bucket(feature: str) -> tuple[int, float]:
    h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
    return h % DIM, 1.0 if (h >> 63) & 1 else -1.0


def hash_embed(text: str) -> list[float]:
    toks = tokenize(text)
    vec = [0.0] * DIM
    counts: dict[str, float] = {}
    for t in toks:
        counts[f"w:{t}"] = counts.get(f"w:{t}", 0) + 1.0
        for i in range(len(t) - 2):
            counts[f"c:{t[i:i+3]}"] = counts.get(f"c:{t[i:i+3]}", 0) + 0.25
    for a, b in zip(toks, toks[1:], strict=False):
        counts[f"b:{a}_{b}"] = counts.get(f"b:{a}_{b}", 0) + 0.6
    for f, c in counts.items():
        i, sign = _bucket(f)
        vec[i] += sign * (1.0 + math.log(c)) if c >= 1 else sign * c
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


class Embedder:
    @property
    def model_name(self) -> str:
        return f"openai:{settings.openai_embedding_model}" if settings.openai_embeddings_enabled else "local-hash-384"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if settings.openai_embeddings_enabled:
            from langchain_openai import OpenAIEmbeddings
            client = OpenAIEmbeddings(model=settings.openai_embedding_model, dimensions=DIM, api_key=settings.openai_api_key,
                                      base_url=settings.openai_base_url or None)
            return await client.aembed_documents(texts)
        return [hash_embed(t) for t in texts]

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


embedder = Embedder()


def vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
