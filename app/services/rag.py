"""Retrieval-augmented generation over pgvector.

Every chunk stores two vectors:
* ``lexical``   - a local feature-hashing embedding (no model needed, always available)
* ``embedding`` - an optional semantic embedding from the AI gateway (EMBEDDING_MODEL)

Search runs both rankings and fuses them with Reciprocal Rank Fusion, so retrieval keeps
working (lexically) even when the AI gateway is offline.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from sqlalchemy import and_, delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LEXICAL_DIM, KnowledgeChunk
from .ai import ai

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = set("""a an and are as at be been but by can could did do does for from had has have he her his how i if
in into is it its me my no not of on or our she so than that the their them then there these they this to too us
was we were what when where which who why will with would you your yours about also just very more most some such
only own same each other any all both few""".split())


def _stem(token: str) -> str:
    for suffix in ("ingly", "ing", "edly", "ed", "es", "ly", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def lexical_embedding(content: str, dim: int = LEXICAL_DIM) -> list[float]:
    tokens = [_stem(t) for t in TOKEN_RE.findall(content.lower()) if t not in STOPWORDS and len(t) > 1]
    features = tokens + [f"{a}_{b}" for a, b in pairwise(tokens)]
    vec = np.zeros(dim, dtype=np.float32)
    for feat in features:
        h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "little")
        vec[h % dim] += 1.0 if (h >> 63) & 1 else -1.0
    vec = np.sign(vec) * np.log1p(np.abs(vec))
    norm = float(np.linalg.norm(vec))
    if norm == 0:
        vec[0], norm = 1.0, 1.0
    return (vec / norm).tolist()


def chunk_text(content: str, max_chars: int = 1000, overlap: int = 150) -> list[str]:
    content = re.sub(r"[ \t]+", " ", content.replace("\r", "")).strip()
    if not content:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
    pieces: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            pieces.append(para)
            continue
        sentence_buf = ""
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            if len(sentence_buf) + len(sentence) + 1 > max_chars and sentence_buf:
                pieces.append(sentence_buf.strip())
                sentence_buf = ""
            while len(sentence) > max_chars:  # very long "sentence"
                pieces.append(sentence[:max_chars])
                sentence = sentence[max_chars - overlap:]
            sentence_buf += " " + sentence
        if sentence_buf.strip():
            pieces.append(sentence_buf.strip())
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        if buf and len(buf) + len(piece) + 2 > max_chars:
            chunks.append(buf)
            tail = buf[-overlap:]
            buf = (tail[tail.find(" ") + 1:] if " " in tail else tail) + "\n" + piece
        else:
            buf = f"{buf}\n\n{piece}" if buf else piece
    if buf:
        chunks.append(buf)
    return chunks[:400]


async def index_document(db: AsyncSession, *, source_type: str, source_id: int, title: str, content: str,
                         batch_id: int | None = None, class_level: str | None = None,
                         subject: str | None = None) -> int:
    """(Re)index a document. Caller commits."""
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_type == source_type,
                                                  KnowledgeChunk.source_id == source_id))
    chunks = chunk_text(content)
    if not chunks:
        return 0
    semantic, model_name = await ai.embed([f"{title}\n{c}" for c in chunks])
    db.add_all([
        KnowledgeChunk(
            source_type=source_type, source_id=source_id, batch_id=batch_id, class_level=class_level,
            subject=subject, title=title[:200], content=chunk, lexical=lexical_embedding(f"{title}\n{chunk}"),
            embedding=semantic[i] if semantic else None, embed_model=model_name,
        )
        for i, chunk in enumerate(chunks)
    ])
    return len(chunks)


async def remove_document(db: AsyncSession, source_type: str, source_id: int) -> None:
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_type == source_type,
                                                  KnowledgeChunk.source_id == source_id))


@dataclass
class Hit:
    id: int
    title: str
    content: str
    source_type: str
    source_id: int
    score: float


async def search(db: AsyncSession, query: str, *, batch_ids: list[int] | None = None,
                 class_level: str | None = None, subject: str | None = None,
                 source_types: list[str] | None = None, sources: list[tuple[str, int]] | None = None,
                 k: int = 6) -> list[Hit]:
    conditions = []
    scope = []
    if batch_ids:
        scope.append(KnowledgeChunk.batch_id.in_(batch_ids))
    if class_level and subject:
        scope.append(and_(KnowledgeChunk.class_level == class_level, KnowledgeChunk.subject == subject))
    if scope:
        conditions.append(or_(*scope))
    if source_types:
        conditions.append(KnowledgeChunk.source_type.in_(source_types))
    if sources:
        conditions.append(or_(*[and_(KnowledgeChunk.source_type == t, KnowledgeChunk.source_id == i)
                                for t, i in sources]))
    await db.execute(text("SET LOCAL hnsw.ef_search = 200"))
    cols = (KnowledgeChunk.id, KnowledgeChunk.title, KnowledgeChunk.content, KnowledgeChunk.source_type,
            KnowledgeChunk.source_id)

    lex_dist = KnowledgeChunk.lexical.cosine_distance(lexical_embedding(query)).label("dist")
    lex_rows = (await db.execute(select(*cols, lex_dist).where(*conditions).order_by(lex_dist).limit(k * 3))).all()
    rankings = [[r for r in lex_rows if r.dist is not None and r.dist < 0.97]]

    semantic, model_name = await ai.embed([query])
    if semantic:
        sem_dist = KnowledgeChunk.embedding.cosine_distance(semantic[0]).label("dist")
        sem_rows = (await db.execute(
            select(*cols, sem_dist).where(*conditions, KnowledgeChunk.embed_model == model_name)
            .order_by(sem_dist).limit(k * 3))).all()
        rankings.append(list(sem_rows))

    scores: dict[int, float] = {}
    rows = {}
    for ranking in rankings:
        for rank, row in enumerate(ranking):
            scores[row.id] = scores.get(row.id, 0.0) + 1.0 / (60 + rank)
            rows.setdefault(row.id, row)
    best = sorted(scores, key=scores.__getitem__, reverse=True)[:k]
    return [Hit(r.id, r.title, r.content, r.source_type, r.source_id, round(scores[i], 4))
            for i in best for r in [rows[i]]]


def format_context(hits: list[Hit], max_chars: int = 7000) -> str:
    parts, used = [], 0
    for n, hit in enumerate(hits, 1):
        block = f"[{n}] {hit.title}\n{hit.content.strip()}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "<context>\n" + "\n\n".join(parts) + "\n</context>" if parts else "<context>\n(no notes found)\n</context>"
