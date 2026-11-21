"""Corpus loading and BM25 search (Phase 1: lexical only; hybrid search arrives in Phase 2).

Version preference is a metadata filter applied before ranking, never a prompt instruction.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

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


class Bm25Index:
    def __init__(self, passages: Sequence[CorpusPassage]) -> None:
        self.passages = list(passages)
        self.by_id = {p.passage_id: p for p in self.passages}
        self._bm25 = BM25Okapi([tokenize(p.text) for p in self.passages]) if passages else None

    def search(
        self,
        query: str,
        *,
        as_of: date,
        k: int,
        score_floor: Decimal,
        doc_filter: Sequence[str] | None = None,
    ) -> list[Passage]:
        terms = tokenize(query)
        if self._bm25 is None or not terms:
            return []
        scores = self._bm25.get_scores(terms)
        allowed = set(doc_filter) if doc_filter else None
        ranked = []
        for p, raw in zip(self.passages, scores, strict=True):
            if allowed is not None and p.doc_id not in allowed:
                continue
            if not p.in_force(as_of):
                continue
            # A retrieval score, never a reported number; rounded so the output is stable.
            score = Decimal(str(round(float(raw), 4)))
            if score > 0 and score >= score_floor:
                ranked.append((score, p))
        ranked.sort(key=lambda sp: (-sp[0], sp[1].passage_id))
        return [to_passage(p, s) for s, p in ranked[:k]]
