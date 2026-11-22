"""Fetch the corpus documents named in corpus/manifest.yaml into corpus/raw/ (docs/07).

Each file is verified against the manifest's sha256 when one is recorded; otherwise its
hash is printed so the domain reviewer can record it. Refuses an unsigned manifest unless
--allow-unsigned (development only). Documents without a URL or version are skipped and
listed. Raw files are git-ignored and never committed.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import httpx

from auditguard.config import REPO_ROOT, load_settings, resolve_path
from auditguard.corpus.manifest import Document, load_manifest

RAW = REPO_ROOT / "corpus" / "raw"
SUFFIX = {"html": ".html", "pdf": ".pdf", "text": ".txt"}


def raw_path(doc: Document) -> Path:
    return RAW / f"{doc.doc_id}{SUFFIX[doc.format]}"


def fetch(doc: Document, client: httpx.Client) -> str:
    if doc.url is None:
        raise ValueError(f"{doc.doc_id}: no url in the manifest")
    response = client.get(doc.url, follow_redirects=True, timeout=60)
    response.raise_for_status()
    data = response.content
    digest = hashlib.sha256(data).hexdigest()
    if doc.sha256 and digest != doc.sha256:
        raise ValueError(f"{doc.doc_id}: sha256 {digest} does not match the manifest")
    RAW.mkdir(parents=True, exist_ok=True)
    raw_path(doc).write_bytes(data)
    return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-unsigned", action="store_true")
    args = parser.parse_args(argv)
    manifest = load_manifest(resolve_path(load_settings().corpus.manifest))
    if not manifest.signed and not args.allow_unsigned:
        print("corpus/manifest.yaml is not signed by the domain reviewer; refusing to fetch")
        print("(development only: --allow-unsigned)")
        return 2
    ready = [d for d in manifest.documents if d.ready]
    for d in manifest.documents:
        if not d.ready:
            print(f"skip {d.doc_id}: needs {'url' if not d.url else 'version'} in the manifest")
    with httpx.Client(headers={"User-Agent": "auditguard-corpus-fetch/1"}) as client:
        for d in ready:
            digest = fetch(d, client)
            note = "verified" if d.sha256 else "record this sha256 in the manifest"
            print(f"{d.doc_id}: {raw_path(d).name} sha256={digest} ({note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
