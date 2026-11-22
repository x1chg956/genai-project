"""Corpus loading and BM25 search. `hybrid.py` adds vectors, fusion, reranking and
decomposition on top (Phase 2, docs/07); with those switched off this is the whole search.

Version preference is a metadata filter applied before ranking, never a prompt instruction.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from rank_bm25 import BM25Okapi

from auditguard.schemas import Passage
from mcp_servers.regulatory_kb.models import CorpusPassage

_WORD = re.compile(r"[a-z0-9]+")


class CorpusError(ValueError):
    """The passages file is missing or invalid."""


def tokenize(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def load_corpus(path: Path) -> list[CorpusPassage]:
    if not path.is_file():
        raise CorpusError(f"no passages file at {path}; run make ingest")
    passages = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                passages.append(CorpusPassage.model_validate_json(line))
            except ValueError as exc:
                raise CorpusError(f"{path.name}:{n}: {exc}") from None
    ids = [p.passage_id for p in passages]
    if len(ids) != len(set(ids)):
        raise CorpusError(f"{path.name}: duplicate passage_id")
    return passages


def to_passage(p: CorpusPassage, score: Decimal) -> Passage:
    return Passage(
        passage_id=p.passage_id,
        doc_id=p.doc_id,
        version=p.version,
        clause_ref=p.clause_ref,
        effective_from=p.effective_from,
        text=p.text,
        score=score,
    )


Kind = Literal["regulation", "typology"]


def retrieval_score(raw: float) -> Decimal:
    """A retrieval score, never a reported number; rounded so the output is stable."""
    return Decimal(str(round(raw, 4)))


def eligible(
    p: CorpusPassage, *, kind: Kind, as_of: date | None, doc_filter: set[str] | None
) -> bool:
    """The metadata filter, applied before any ranking. Typologies have no version filter."""
    if p.kind != kind or (doc_filter is not None and p.doc_id not in doc_filter):
        return False
    return as_of is None or p.in_force(as_of)


class Bm25Index:
    def __init__(self, passages: Sequence[CorpusPassage]) -> None:
        self.passages = list(passages)
        self.by_id = {p.passage_id: p for p in self.passages}
        self._bm25 = BM25Okapi([tokenize(p.text) for p in self.passages]) if passages else None

    def ranked(self, query: str, allowed: set[str], k: int) -> list[tuple[str, Decimal]]:
        """Top k (passage_id, BM25 score > 0) among `allowed`, best first."""
        terms = tokenize(query)
        if self._bm25 is None or not terms:
            return []
        scores = self._bm25.get_scores(terms)
        out = [
            (p.passage_id, retrieval_score(float(raw)))
            for p, raw in zip(self.passages, scores, strict=True)
            if p.passage_id in allowed
        ]
        out = [(pid, s) for pid, s in out if s > 0]
        out.sort(key=lambda x: (-x[1], x[0]))
        return out[:k]

    def allowed(
        self, *, kind: Kind, as_of: date | None, doc_filter: Sequence[str] | None
    ) -> set[str]:
        docs = set(doc_filter) if doc_filter else None
        return {
            p.passage_id
            for p in self.passages
            if eligible(p, kind=kind, as_of=as_of, doc_filter=docs)
        }

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
        allowed = self.allowed(kind=kind, as_of=as_of, doc_filter=doc_filter)
        ranked = [
            (pid, s) for pid, s in self.ranked(query, allowed, len(allowed)) if s >= score_floor
        ]
        return [to_passage(self.by_id[pid], s) for pid, s in ranked[:k]]
