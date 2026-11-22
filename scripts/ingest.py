"""Ingest the fetched corpus (docs/07): parse, chunk by clause, write passages, index vectors.

1. For each manifest document with a raw file in corpus/raw/: parse to text, chunk by
   clause (auditguard/corpus/chunk.py), passage_id = "{doc_id}:{clause_ref}:{version}".
2. Write corpus/processed/passages.jsonl (one CorpusPassage per line, sorted, stable).
3. Fill the Qdrant collection passages_<corpus_version> with bge-small vectors (skipped with
   --no-vectors). BM25 is rebuilt from the passages at server start (milliseconds).
Run `make ingest` (fetch + ingest). Documents without a raw file are listed and skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from auditguard.config import REPO_ROOT, Settings, load_settings, resolve_path
from auditguard.corpus.chunk import chunk_clauses
from auditguard.corpus.manifest import Document, load_manifest
from auditguard.corpus.parse import html_to_text, pdf_to_text
from mcp_servers.regulatory_kb.models import CorpusPassage
from scripts.fetch_corpus import raw_path

OUT = REPO_ROOT / "corpus" / "processed" / "passages.jsonl"


def passages_for(doc: Document, raw: bytes, max_chars: int = 1500) -> list[CorpusPassage]:
    if doc.version is None:
        raise ValueError(f"{doc.doc_id}: no version in the manifest")
    if doc.format == "pdf":
        text = pdf_to_text(raw)
    elif doc.format == "html":
        text = html_to_text(raw.decode("utf-8", errors="replace"))
    else:
        text = raw.decode("utf-8", errors="replace")
    return [
        CorpusPassage(
            passage_id=f"{doc.doc_id}:{c.clause_ref}:{doc.version}",
            doc_id=doc.doc_id,
            title=doc.title,
            version=doc.version,
            clause_ref=c.clause_ref,
            effective_from=doc.effective_from,
            superseded_on=doc.superseded_on,
            superseded_by=doc.superseded_by,
            kind=doc.kind,
            issuer=doc.issuer,
            published=doc.published,
            text=c.text,
        )
        for c in chunk_clauses(text, max_chars)
    ]


def write_passages(passages: list[CorpusPassage], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(passages, key=lambda p: p.passage_id)
    out.write_text(
        "".join(json.dumps(p.model_dump(mode="json"), sort_keys=True) + "\n" for p in rows),
        encoding="utf-8",
    )


def index_vectors(settings: Settings, passages: list[CorpusPassage], out: Path) -> str:
    from qdrant_client import QdrantClient

    from mcp_servers.regulatory_kb.hybrid import FastEmbedder, QdrantVectors
    from mcp_servers.regulatory_kb.server import corpus_version

    r = settings.retrieval
    collection = f"passages_{corpus_version(out)}"
    vectors = QdrantVectors(
        QdrantClient(url=settings.qdrant.url),
        collection,
        FastEmbedder(r.embedding_model, resolve_path(r.model_cache)),
    )
    added = vectors.ensure(passages)
    return f"{collection}: {added} vectors added" if added else f"{collection}: already built"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-vectors", action="store_true")
    args = parser.parse_args(argv)
    settings = load_settings()
    manifest = load_manifest(resolve_path(settings.corpus.manifest))
    passages: list[CorpusPassage] = []
    for doc in manifest.documents:
        path = raw_path(doc)
        if not doc.ready or not path.is_file():
            print(f"skip {doc.doc_id}: not fetched (make ingest fetches signed, complete entries)")
            continue
        found = passages_for(doc, path.read_bytes())
        print(f"{doc.doc_id} {doc.version}: {len(found)} passages")
        passages += found
    if not passages:
        print(
            "no passages: nothing fetched yet (the manifest needs the domain reviewer's sign-off)"
        )
        return 2
    write_passages(passages, OUT)
    print(f"wrote {OUT.relative_to(REPO_ROOT)}: {len(passages)} passages")
    if not args.no_vectors:
        print(index_vectors(settings, passages, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
