"""Hybrid retrieval v1 (docs/07): BM25 + bge vectors in Qdrant, Reciprocal Rank Fusion,
cross-encoder rerank and query decomposition. Each step is switched by `retrieval.*`, so the
E5 staircase measures one step at a time.

Order per call:
1. Metadata filter first (kind, in-force version on as_of_date, doc_filter): only eligible
   passages are ranked, so a superseded clause can never outrank the one in force.
2. Queries: the agent's query, plus at most 3 sub-queries from the decomposer when it is on.
   The original query is always kept, so a poor decomposition cannot lose recall.
3. Per query: BM25 top k_bm25 (score > 0) and vector top k_vector (cosine >= vector_floor).
4. One ranked list → its own scores; several lists → RRF, score = sum 1 / (rrf_k + rank).
5. Rerank on: the top rerank_candidates by fused order are scored by the cross-encoder; the
   score is the sigmoid of its logit (0..1) and passages below score_floor are dropped.
   Rerank off with BM25 alone keeps the Phase 1 rule: BM25 score >= score_floor.
6. Top k. None left → the caller reports status "no_support".

Passage text is untrusted data: it is embedded and scored, never followed.
Scores are retrieval scores, never reported numbers; they are rounded to 4 places.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from auditguard.schemas import Passage
from mcp_servers.regulatory_kb.index import Bm25Index, Kind, retrieval_score, to_passage
from mcp_servers.regulatory_kb.models import CorpusPassage

MAX_SUBQUERIES = 3
_NS = uuid.UUID("6f1d2a8e-3c4b-4f5a-9e2d-0a1b2c3d4e5f")  # passage_id → Qdrant point id


class Embedder(Protocol):
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        """One raw relevance logit per text, same order."""
        ...


Decomposer = Callable[[str], list[str]]


class VectorIndex(Protocol):
    def ranked(self, query: str, allowed: set[str], k: int) -> list[tuple[str, float]]:
        """Top k (passage_id, cosine) among `allowed`, best first."""
        ...


@dataclass(frozen=True)
class Switches:
    bm25: bool
    vector: bool
    rerank: bool
    decompose: bool
    k_bm25: int
    k_vector: int
    rrf_k: int
    rerank_candidates: int
    vector_floor: Decimal

    def __post_init__(self) -> None:
        if not (self.bm25 or self.vector):
            raise ValueError("retrieval needs bm25 or vector switched on")


def point_id(passage_id: str) -> str:
    return str(uuid.uuid5(_NS, passage_id))


def rrf(lists: Sequence[Sequence[str]], k: int) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: score(d) = sum over lists of 1 / (k + rank), rank from 1.
    Ties by passage_id, so the order is stable."""
    scores: dict[str, float] = {}
    for ranked in lists:
        for rank, pid in enumerate(ranked, start=1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))


class QdrantVectors:
    """Passages embedded into one Qdrant collection per corpus version (cosine distance)."""

    def __init__(self, client: Any, collection: str, embedder: Embedder) -> None:
        self.client, self.collection, self.embedder = client, collection, embedder

    def ensure(self, passages: Sequence[CorpusPassage], batch: int = 64) -> int:
        """Create and fill the collection if it does not exist yet. Returns points added."""
        from qdrant_client import models

        if self.client.collection_exists(self.collection):
            return 0
        self.client.create_collection(
            self.collection,
            vectors_config=models.VectorParams(
                size=self.embedder.dim, distance=models.Distance.COSINE
            ),
        )
        for i in range(0, len(passages), batch):
            chunk = passages[i : i + batch]
            vectors = self.embedder.embed([p.text for p in chunk])
            self.client.upsert(
                self.collection,
                points=[
                    models.PointStruct(
                        id=point_id(p.passage_id), vector=v, payload={"passage_id": p.passage_id}
                    )
                    for p, v in zip(chunk, vectors, strict=True)
                ],
            )
        return len(passages)

    def ranked(self, query: str, allowed: set[str], k: int) -> list[tuple[str, float]]:
        from qdrant_client import models

        if not allowed:
            return []
        hits = self.client.query_points(
            self.collection,
            query=self.embedder.embed([query])[0],
            query_filter=models.Filter(
                must=[models.HasIdCondition(has_id=[point_id(p) for p in sorted(allowed)])]
            ),
            limit=k,
            with_payload=True,
        ).points
        return [(h.payload["passage_id"], float(h.score)) for h in hits]


class HybridRetriever:
    def __init__(
        self,
        passages: Sequence[CorpusPassage],
        switches: Switches,
        *,
        vectors: VectorIndex | None = None,
        reranker: Reranker | None = None,
        decomposer: Decomposer | None = None,
    ) -> None:
        if switches.vector and vectors is None:
            raise ValueError("retrieval.vector is on but no vector index was given")
        if switches.rerank and reranker is None:
            raise ValueError("retrieval.rerank is on but no reranker was given")
        if switches.decompose and decomposer is None:
            raise ValueError("retrieval.decompose is on but no decomposer was given")
        self.bm25 = Bm25Index(passages)
        self.passages, self.by_id = self.bm25.passages, self.bm25.by_id
        self.sw, self.vectors, self.reranker, self.decomposer = (
            switches,
            vectors,
            reranker,
            decomposer,
        )

    def queries(self, query: str) -> list[str]:
        out = [query]
        if self.sw.decompose and self.decomposer is not None:
            try:
                subs = self.decomposer(query)
            except Exception:  # a failed decomposition falls back to the query
                subs = []
            for q in subs[:MAX_SUBQUERIES]:
                q = q.strip()
                if q and q.casefold() not in {x.casefold() for x in out}:
                    out.append(q)
        return out

    def search(
        self,
        query: str,
        *,
        as_of: date | None,
        k: int,
        score_floor: Decimal,
        doc_filter: Sequence[str] | None = None,
        kind: Kind = "regulation",
    ) -> list[Passage]:
        allowed = self.bm25.allowed(kind=kind, as_of=as_of, doc_filter=doc_filter)
        if not allowed:
            return []
        lists: list[list[tuple[str, float]]] = []
        for q in self.queries(query):
            if self.sw.bm25:
                lists.append(
                    [(p, float(s)) for p, s in self.bm25.ranked(q, allowed, self.sw.k_bm25)]
                )
            if self.sw.vector and self.vectors is not None:
                hits = self.vectors.ranked(q, allowed, self.sw.k_vector)
                lists.append([(p, s) for p, s in hits if Decimal(str(s)) >= self.sw.vector_floor])
        lists = [x for x in lists if x]
        if not lists:
            return []
        fused = (
            lists[0] if len(lists) == 1 else rrf([[p for p, _ in x] for x in lists], self.sw.rrf_k)
        )

        if self.sw.rerank and self.reranker is not None:
            top = [pid for pid, _ in fused[: self.sw.rerank_candidates]]
            logits = self.reranker.scores(query, [self.by_id[p].text for p in top])
            scored = [(p, retrieval_score(sigmoid(x))) for p, x in zip(top, logits, strict=True)]
            scored = [(p, s) for p, s in scored if s >= score_floor]
            scored.sort(key=lambda x: (-x[1], x[0]))
        else:
            scored = [(p, retrieval_score(s)) for p, s in fused]
            if self.sw.bm25 and not self.sw.vector and len(lists) == 1:
                scored = [(p, s) for p, s in scored if s >= score_floor]  # Phase 1 rule
        return [to_passage(self.by_id[p], s) for p, s in scored[:k]]


# --------------------------------------------------------------------------- live components


class FastEmbedder:
    """BAAI/bge-small-en-v1.5 (or the configured model) on CPU via fastembed (ONNX)."""

    def __init__(self, model: str, cache_dir: Path) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model, cache_dir=str(cache_dir))
        self.dim = len(next(iter(self._model.embed(["probe"]))))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(x) for x in v] for v in self._model.embed(list(texts))]


class FastReranker:
    """cross-encoder/ms-marco-MiniLM-L-6-v2 (ONNX export) on CPU via fastembed."""

    def __init__(self, model: str, cache_dir: Path) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model, cache_dir=str(cache_dir))

    def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        return [float(x) for x in self._model.rerank(query, list(texts))] if texts else []
