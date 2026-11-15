"""Corpus chunking, indexing and hybrid retrieval on Postgres (pgvector + full-text search).

Hybrid retrieval = dense cosine search + sparse full-text search, fused with Reciprocal Rank
Fusion, then optionally re-ranked by a cross-encoder. Region is a filter: a user in India gets
IN + GLOBAL documents; in the US, US + GLOBAL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
import yaml
from pgvector.psycopg import register_vector

from nip.config import get_settings
from nip.domain.models import Citation, Region
from nip.rag.embeddings import Embedder, get_embedder

CORPUS_DIR = Path(__file__).resolve().parents[4] / "data" / "corpus"
RRF_K = 60
STOPWORDS = set("""a an and are as at be by can do does for from has have how i if in is it its me my
of on or should so than that the their them then there these this to was what when which who why will
with you your am we our much many any about into""".split())


@dataclass
class Chunk:
    id: str
    doc_id: str
    title: str
    section: str
    source: str
    url: str | None
    region: str
    topic: str
    text: str

    def as_citation(self, score: float | None = None) -> Citation:
        snippet = self.text if len(self.text) <= 600 else self.text[:597] + "..."
        return Citation(chunk_id=self.id, source=self.source, title=f"{self.title} - {self.section}",
                        url=self.url, snippet=snippet, score=score)


def load_corpus(corpus_dir: Path = CORPUS_DIR) -> list[Chunk]:
    """One chunk per '## ' section; the document title is kept with each chunk for context."""
    chunks: list[Chunk] = []
    for path in sorted(corpus_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
        if not m:
            continue
        meta = yaml.safe_load(m.group(1))
        body = m.group(2)
        sections = re.split(r"^## ", body, flags=re.M)
        n = 0
        for sec in sections:
            sec = sec.strip()
            if not sec:
                continue
            heading, _, text = sec.partition("\n")
            n += 1
            chunks.append(Chunk(
                id=f"{path.stem}#{n}", doc_id=path.stem, title=meta["title"], section=heading.strip(),
                source=meta["source"], url=meta.get("url"), region=meta.get("region", "GLOBAL"),
                topic=meta.get("topic", ""), text=" ".join(text.split()),
            ))
    return chunks


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS kb_chunks (
  id text PRIMARY KEY,
  doc_id text NOT NULL,
  title text NOT NULL,
  section text NOT NULL,
  source text NOT NULL,
  url text,
  region text NOT NULL,
  topic text,
  body text NOT NULL,
  embedding vector({dim}),
  tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english', title || ' ' || section), 'A') ||
    setweight(to_tsvector('english', body), 'B')) STORED
);
CREATE INDEX IF NOT EXISTS kb_chunks_tsv ON kb_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS kb_chunks_region ON kb_chunks (region);
"""


def connect(url: str | None = None) -> psycopg.Connection:
    conn = psycopg.connect(url or get_settings().database_url, autocommit=True)
    return conn


def index_corpus(conn: psycopg.Connection, embedder: Embedder | None = None,
                 chunks: list[Chunk] | None = None) -> int:
    embedder = embedder or get_embedder()
    chunks = chunks if chunks is not None else load_corpus()
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    conn.execute("DROP TABLE IF EXISTS kb_chunks")
    conn.execute(SCHEMA.format(dim=embedder.dim))
    vectors = embedder.embed_documents([f"{c.title}. {c.section}. {c.text}" for c in chunks])
    with conn.cursor() as cur:
        for c, v in zip(chunks, vectors, strict=True):
            cur.execute(
                "INSERT INTO kb_chunks (id, doc_id, title, section, source, url, region, topic, body, embedding)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)",
                (c.id, c.doc_id, c.title, c.section, c.source, c.url, c.region, c.topic, c.text, v),
            )
    return len(chunks)


def _or_query(text: str) -> str:
    toks = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS and len(t) > 1]
    return " | ".join(dict.fromkeys(toks))


def _row_to_chunk(r: tuple) -> Chunk:
    return Chunk(id=r[0], doc_id=r[1], title=r[2], section=r[3], source=r[4], url=r[5], region=r[6],
                 topic=r[7], text=r[8])


COLS = "id, doc_id, title, section, source, url, region, topic, body"


class Retriever:
    def __init__(self, conn: psycopg.Connection, embedder: Embedder | None = None, reranker=None):
        self.conn = conn
        register_vector(conn)
        self.embedder = embedder or get_embedder()
        self.reranker = reranker

    def _regions(self, region: Region | str | None) -> list[str]:
        r = region.value if isinstance(region, Region) else region
        return ["GLOBAL", r] if r else ["GLOBAL", "IN", "US"]

    def dense(self, query: str, regions: list[str], k: int) -> list[tuple[Chunk, float]]:
        v = self.embedder.embed_query(query)
        rows = self.conn.execute(
            f"SELECT {COLS}, 1 - (embedding <=> %s::vector) AS score FROM kb_chunks"  # noqa: S608 (constant cols)
            " WHERE region = ANY(%s) ORDER BY embedding <=> %s::vector LIMIT %s",
            (v, regions, v, k),
        ).fetchall()
        return [(_row_to_chunk(r), float(r[9])) for r in rows]

    def sparse(self, query: str, regions: list[str], k: int) -> list[tuple[Chunk, float]]:
        q = _or_query(query)
        if not q:
            return []
        rows = self.conn.execute(
            f"SELECT {COLS}, ts_rank_cd(tsv, to_tsquery('english', %s)) AS score FROM kb_chunks"  # noqa: S608
            " WHERE region = ANY(%s) AND tsv @@ to_tsquery('english', %s) ORDER BY score DESC LIMIT %s",
            (q, regions, q, k),
        ).fetchall()
        return [(_row_to_chunk(r), float(r[9])) for r in rows]

    def search(self, query: str, region: Region | str | None = None, k: int = 5,
               mode: str = "hybrid", candidates: int = 20) -> list[tuple[Chunk, float]]:
        regions = self._regions(region)
        if mode == "dense":
            results = self.dense(query, regions, max(k, candidates))
        elif mode == "sparse":
            results = self.sparse(query, regions, max(k, candidates))
        else:
            fused: dict[str, float] = {}
            by_id: dict[str, Chunk] = {}
            for ranked in (self.dense(query, regions, candidates), self.sparse(query, regions, candidates)):
                for rank, (c, _) in enumerate(ranked):
                    fused[c.id] = fused.get(c.id, 0.0) + 1.0 / (RRF_K + rank + 1)
                    by_id[c.id] = c
            results = sorted(((by_id[i], s) for i, s in fused.items()), key=lambda x: -x[1])
        if self.reranker is not None and results:
            results = self.reranker.rerank(query, results[:candidates])
        return results[:k]


class CrossEncoderReranker:
    def __init__(self, model: str):
        from fastembed.rerank.cross_encoder import TextCrossEncoder  # optional dependency

        self._m = TextCrossEncoder(model_name=model)

    def rerank(self, query: str, results: list[tuple[Chunk, float]]) -> list[tuple[Chunk, float]]:
        scores = list(self._m.rerank(query, [f"{c.title}. {c.section}. {c.text}" for c, _ in results]))
        return sorted(((c, float(s)) for (c, _), s in zip(results, scores, strict=True)), key=lambda x: -x[1])


def get_reranker():
    s = get_settings()
    if s.reranker == "fastembed":
        return CrossEncoderReranker(s.reranker_model)
    return None


if __name__ == "__main__":
    with connect() as c:
        print(f"indexed {index_corpus(c)} chunks")
