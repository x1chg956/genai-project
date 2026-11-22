"""corpus/manifest.yaml: one entry per document, signed by the domain reviewer (docs/07)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict


class ManifestError(ValueError):
    """The manifest is invalid, or not signed where a signature is required."""


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    title: str
    issuer: str | None = None
    reference: str | None = None
    version: str | None = None
    published: date | None = None
    effective_from: date | None = None
    superseded_on: date | None = None
    superseded_by: str | None = None
    kind: Literal["regulation", "typology"] = "regulation"
    format: Literal["html", "pdf", "text"]
    url: str | None = None
    licence_note: str | None = None
    sha256: str | None = None

    @property
    def ready(self) -> bool:
        """Has everything needed to fetch and index it."""
        return bool(self.url and self.version)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signed_by: str | None = None
    signed_on: date | None = None
    documents: list[Document]

    @property
    def signed(self) -> bool:
        return bool(self.signed_by and self.signed_on)


def load_manifest(path: Path) -> Manifest:
    if not path.is_file():
        raise ManifestError(f"no manifest at {path}")
    m = Manifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    ids = [d.doc_id for d in m.documents]
    if len(ids) != len(set(ids)):
        raise ManifestError("duplicate doc_id in manifest")
    return m
