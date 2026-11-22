"""Corpus records and tool outputs of the regulatory_kb server."""

from __future__ import annotations

from datetime import date
from typing import Literal

from auditguard.schemas import Model, Passage


class CorpusPassage(Model):
    """One clause as written by scripts/ingest.py (one JSON object per line)."""

    passage_id: str
    """`{doc_id}:{clause_ref}:{version}`, stable across runs."""
    doc_id: str
    title: str
    version: str
    clause_ref: str
    effective_from: date | None = None
    superseded_on: date | None = None
    """Date the document stopped applying; None while it is current."""
    kind: Literal["regulation", "typology"] = "regulation"
    text: str
    issuer: str | None = None
    published: date | None = None
    superseded_by: str | None = None
    """doc_id of the document that replaces this one (manifest metadata)."""

    def in_force(self, as_of: date) -> bool:
        started = self.effective_from is None or self.effective_from <= as_of
        return started and (self.superseded_on is None or self.superseded_on > as_of)


class PassageSearch(Model):
    status: Literal["ok", "no_support"]
    query: str
    as_of_date: date
    passages: list[Passage]


class TypologySearch(Model):
    """Public typology material (kind `typology`): patterns, not rules in force."""

    status: Literal["ok", "no_support"]
    query: str
    passages: list[Passage]


class DocVersion(Model):
    doc_id: str
    title: str
    version: str
    effective_from: date | None
    superseded_on: date | None
    superseded_by: str | None
    passage_count: int


class DocVersions(Model):
    doc_id: str
    versions: list[DocVersion]
    """Oldest first: by effective_from, then version."""
