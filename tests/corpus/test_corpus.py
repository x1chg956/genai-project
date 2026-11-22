"""Corpus tooling: manifest, parsing, clause-aware chunking, ingest (docs/07). Synthetic text."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auditguard.config import REPO_ROOT
from auditguard.corpus.chunk import chunk_clauses
from auditguard.corpus.manifest import Document, ManifestError, load_manifest
from auditguard.corpus.parse import html_to_text
from scripts import fetch_corpus
from scripts.ingest import passages_for, write_passages

TEXT = """Synthetic test rules (not regulatory text)
1. Short title. These are test rules.
2. Reporting. (1) The test entity furnishes a report within seven days.
(2) The report carries:
(a) the account;
(b) the grounds;
(i) as observed;
(ii) as explained.
3. Records are kept for five years.
"""


def test_one_chunk_per_clause_when_short() -> None:
    chunks = chunk_clauses(TEXT, max_chars=1500)
    assert [c.clause_ref for c in chunks] == ["preamble", "1", "2", "3"]
    assert chunks[2].text.startswith("2. Reporting. (1)") and "(ii) as explained." in chunks[2].text


def test_long_clause_splits_at_sub_clauses_with_nested_refs() -> None:
    refs = [c.clause_ref for c in chunk_clauses(TEXT, max_chars=40)]
    assert refs == [
        "preamble",
        "1",
        "2",
        "2(2)",
        "2(2)(a)",
        "2(2)(b)",
        "2(2)(b)(i)",
        "2(2)(b)(ii)",
        "3",
    ]


def test_letter_i_after_h_is_a_letter_and_repeated_refs_get_a_suffix() -> None:
    text = "5. List. (1) Items:\n(h) eighth;\n(i) ninth letter;\n1. Schedule clause one.\n"
    refs = [c.clause_ref for c in chunk_clauses(text, max_chars=10)]
    # "(1)" sits mid-line, so it is not a heading; "(i)" after "(h)" is the letter i
    assert refs == ["5", "5(h)", "5(i)", "1"]
    twice = chunk_clauses("1. One.\n1. One again.\n")
    assert [c.clause_ref for c in twice] == ["1", "1#2"]


def test_html_keeps_blocks_as_lines_tables_as_text_and_drops_scripts() -> None:
    html = (
        "<html><script>ignore()</script><p>1. Clause one.</p><table><tr><td>Limit</td>"
        "<td>INR 10 lakh</td></tr></table><div>2. Clause two.</div></html>"
    )
    assert html_to_text(html).splitlines() == [
        "1. Clause one.",
        "Limit | INR 10 lakh",
        "2. Clause two.",
    ]


def test_repo_manifest_parses_and_is_an_unsigned_draft() -> None:
    m = load_manifest(REPO_ROOT / "corpus" / "manifest.yaml")
    assert not m.signed  # the domain reviewer signs it
    ids = {d.doc_id for d in m.documents}
    assert {"RBI-KYC-CB-2025", "RBI-KYC-MD-2016", "PML-RULES-2005", "PMLA-2002"} <= ids
    old = next(d for d in m.documents if d.doc_id == "RBI-KYC-MD-2016")
    assert old.superseded_by == "RBI-KYC-CB-2025"


def test_manifest_rejects_duplicates(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    doc = {"doc_id": "D", "title": "t", "format": "text"}
    p.write_text(json.dumps({"documents": [doc, doc]}))
    with pytest.raises(ManifestError, match="duplicate"):
        load_manifest(p)


def test_fetch_refuses_an_unsigned_manifest(capsys: pytest.CaptureFixture[str]) -> None:
    assert fetch_corpus.main([]) == 2
    assert "not signed" in capsys.readouterr().out


def test_ingest_writes_stable_passage_ids(tmp_path: Path) -> None:
    doc = Document(
        doc_id="SYN-DOC", title="Synthetic", version="2026-01-01", format="text", url="x"
    )
    passages = passages_for(doc, TEXT.encode())
    assert [p.passage_id for p in passages][:2] == [
        "SYN-DOC:preamble:2026-01-01",
        "SYN-DOC:1:2026-01-01",
    ]
    out = tmp_path / "passages.jsonl"
    write_passages(passages, out)
    first = out.read_text()
    write_passages(list(reversed(passages)), out)
    assert out.read_text() == first  # sorted, byte-stable across runs
