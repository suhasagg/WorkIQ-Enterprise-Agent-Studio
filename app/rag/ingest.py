"""Document ingestion: parse (PDF / Markdown / text) -> metadata -> chunk -> embed -> pgvector."""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass

from app import db
from app.logging_setup import log
from app.rag.embeddings import embedder, tokenize, vec_literal

INJECTION_PATTERNS = [
    (re.compile(r"(?i)ignore (all |any )?(previous|prior|above) (instructions|prompts)"), "instruction_override"),
    (re.compile(r"(?i)(disregard|forget) (your|the) (system|previous) (prompt|instructions)"), "instruction_override"),
    (re.compile(r"(?i)you are now (an?|the) "), "role_reassignment"),
    (re.compile(r"(?i)(system|assistant)\s*:\s*(you must|always|never)"), "fake_system_message"),
    (re.compile(r"(?i)(send|forward|email|export|upload) .{0,60}(password|credential|payroll|secret|api key)"), "exfiltration_request"),
    (re.compile(r"(?i)do not (tell|inform|mention to) (the )?(user|human|approver)"), "concealment"),
]
ALLOWED_EXT = {".pdf": "pdf", ".md": "markdown", ".markdown": "markdown", ".txt": "text"}


def scan_untrusted(text: str) -> list[str]:
    """Flag instruction-like content. Retrieved text is data; flags let downstream agents exclude or label it."""
    return sorted({name for pat, name in INJECTION_PATTERNS if pat.search(text)})


@dataclass
class Segment:
    text: str
    heading: str | None = None
    page: int | None = None


def parse(data: bytes, source_type: str) -> tuple[list[Segment], dict]:
    if source_type == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        segs = [Segment((p.extract_text() or "").strip(), None, i + 1) for i, p in enumerate(reader.pages)]
        info = reader.metadata or {}
        return [s for s in segs if s.text], {"pages": len(reader.pages), "pdf_title": str(info.get("/Title") or "") or None,
                                             "author": str(info.get("/Author") or "") or None}
    text = data.decode("utf-8", errors="replace")
    if source_type == "markdown":
        meta, body = _front_matter(text)
        segs, heading, buf = [], None, []
        for line in body.splitlines():
            m = re.match(r"^(#{1,3})\s+(.*)", line)
            if m:
                if "".join(buf).strip():
                    segs.append(Segment("\n".join(buf).strip(), heading))
                heading, buf = m.group(2).strip(), []
            else:
                buf.append(line)
        if "".join(buf).strip():
            segs.append(Segment("\n".join(buf).strip(), heading))
        h1 = re.search(r"^#\s+(.*)", body, re.M)
        if h1:
            meta.setdefault("title", h1.group(1).strip())
        return segs, meta
    return [Segment(text.strip())] if text.strip() else [], {}


def _front_matter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            v = v.strip()
            meta[k.strip()] = [x.strip() for x in v.strip("[]").split(",") if x.strip()] if v.startswith("[") else v
    return meta, text[m.end():]


def chunk_segments(segs: list[Segment], strategy: str, max_words: int = 170, overlap: int = 30) -> list[Segment]:
    """`heading`: one chunk per section, split by paragraph when long. `window`: fixed sliding word window."""
    out: list[Segment] = []
    for s in segs:
        if strategy == "window":
            words = s.text.split()
            step = max_words - overlap
            for i in range(0, max(len(words), 1), step):
                piece = " ".join(words[i:i + max_words])
                if piece:
                    out.append(Segment(piece, s.heading, s.page))
                if i + max_words >= len(words):
                    break
            continue
        paras, buf, n = [p.strip() for p in re.split(r"\n\s*\n", s.text) if p.strip()], [], 0
        for p in paras:
            w = len(p.split())
            if buf and n + w > max_words:
                out.append(Segment("\n\n".join(buf), s.heading, s.page))
                buf, n = [], 0
            if w > max_words * 1.5:
                out.extend(chunk_segments([Segment(p, s.heading, s.page)], "window", max_words, overlap))
                continue
            buf.append(p)
            n += w
        if buf:
            out.append(Segment("\n\n".join(buf), s.heading, s.page))
    return out


async def _stage(doc_id: str, stage: str, progress: int, status: str = "processing") -> None:
    await db.execute("UPDATE documents SET stage=%s, progress=%s, status=%s WHERE id=%s", (stage, progress, status, doc_id))


async def create_document(*, workspace_id: str, filename: str, data: bytes, created_by: str, title: str | None = None,
                          allowed_roles: list[str] | None = None, chunk_strategy: str = "auto", tags: list[str] | None = None,
                          origin: str = "upload") -> tuple[dict, bool]:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXT:
        raise ValueError("unsupported file type; allowed: PDF, Markdown, text")
    sha = hashlib.sha256(data).hexdigest()
    existing = await db.fetch_one("SELECT * FROM documents WHERE workspace_id=%s AND sha256=%s", (workspace_id, sha))
    if existing:
        return existing, False
    doc_id = db.new_id("doc")
    mime = {"pdf": "application/pdf", "markdown": "text/markdown", "text": "text/plain"}[ALLOWED_EXT[ext]]
    await db.execute(
        """INSERT INTO documents(id, workspace_id, title, filename, mime, source_type, sha256, size_bytes, chunk_strategy,
           metadata, allowed_roles, created_by, origin) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (doc_id, workspace_id, title or filename.rsplit(".", 1)[0], filename, mime, ALLOWED_EXT[ext], sha, len(data),
         chunk_strategy, db.J({"tags": tags or []}), allowed_roles or [], created_by, origin))
    return await db.fetch_one("SELECT * FROM documents WHERE id=%s", (doc_id,)), True


async def ingest_document(doc_id: str, data: bytes, delay: float = 0.0) -> None:
    """Run the ingestion pipeline, recording stage/progress so the UI can show real ingestion state."""
    import asyncio
    doc = await db.fetch_one("SELECT * FROM documents WHERE id=%s", (doc_id,))
    try:
        await _stage(doc_id, "parsing", 15)
        segs, meta = parse(data, doc["source_type"])
        if not segs:
            raise ValueError("no extractable text found")
        await asyncio.sleep(delay)
        await _stage(doc_id, "chunking", 40)
        strategy = doc["chunk_strategy"]
        if strategy == "auto":
            strategy = "heading" if doc["source_type"] == "markdown" else "window"
        chunks = chunk_segments(segs, strategy)
        await asyncio.sleep(delay)
        await _stage(doc_id, "embedding", 65)
        title = meta.get("title") or meta.get("pdf_title") or doc["title"]
        # Contextual chunk headers: the document title is embedded and indexed with every chunk.
        vectors = await embedder.embed([f"{title}\n{c.heading or ''}\n{c.text}" for c in chunks])
        await asyncio.sleep(delay)
        await _stage(doc_id, "indexing", 85)
        doc_flags: set[str] = set()
        async with db.pool().connection() as conn, conn.transaction():
            await conn.execute("DELETE FROM chunks WHERE document_id=%s", (doc_id,))
            for i, (c, v) in enumerate(zip(chunks, vectors, strict=True)):
                flags = scan_untrusted(c.text)
                doc_flags.update(flags)
                await conn.execute(
                    """INSERT INTO chunks(id, document_id, workspace_id, ordinal, doc_title, heading, page, content, token_count, flags, embedding)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)""",
                    (f"{doc_id}_c{i:03d}", doc_id, doc["workspace_id"], i, title, c.heading, c.page, c.text, len(tokenize(c.text)),
                     db.J(flags), vec_literal(v)))
        words = sum(len(c.text.split()) for c in chunks)
        merged = {**doc["metadata"], **{k: v for k, v in meta.items() if v}, "word_count": words,
                  "chunk_strategy_used": strategy}
        await db.execute(
            """UPDATE documents SET status='indexed', stage='indexed', progress=100, chunk_count=%s, metadata=%s, title=%s,
               untrusted_flags=%s, embedding_model=%s, indexed_at=now(), error=NULL WHERE id=%s""",
            (len(chunks), db.J(merged), title, db.J(sorted(doc_flags)), embedder.model_name, doc_id))
    except Exception as e:
        log.warning("ingest_failed", doc_id=doc_id, error=str(e))
        await db.execute("UPDATE documents SET status='failed', stage='failed', error=%s WHERE id=%s", (str(e)[:500], doc_id))
