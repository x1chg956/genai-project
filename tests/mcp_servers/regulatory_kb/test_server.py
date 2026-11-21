"""regulatory_kb server over a synthetic test corpus (tests/fixtures/passages_test.jsonl)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from auditguard.config import REPO_ROOT
from mcp_servers.regulatory_kb.index import Bm25Index, CorpusError, load_corpus
from mcp_servers.regulatory_kb.server import build_server
from tests.mcp_servers.helpers import CASE, OTHER_CASE, Called, call, guard, token

CORPUS = REPO_ROOT / "tests" / "fixtures" / "passages_test.jsonl"


def server() -> FastMCP:
    return build_server(
        Bm25Index(load_corpus(CORPUS)),
        guard("regulatory_kb"),
        k_default=5,
        score_floor=Decimal("0.2"),
    )


def run(tool: str, agent: str = "researcher", **args: Any) -> Called:
    return call(server(), tool, {"agent_token": token(agent), "case_id": CASE} | args)


def ids(r: Called) -> list[str]:
    return [p["passage_id"] for p in r.result["passages"]]


@pytest.mark.tool("search_passages")
def test_search_passages_happy() -> None:
    r = run(
        "search_passages",
        query="cash deposits below reporting threshold structuring",
        as_of_date="2026-06-01",
    )
    assert not r.is_error and r.result["status"] == "ok"
    assert ids(r)[0] == "TEST-DOC-A:1(2):2026-01-01"
    scores = [Decimal(p["score"]) for p in r.result["passages"]]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.tool("search_passages")
def test_search_passages_version_filter() -> None:
    q = "suspicious transaction report filed within days"
    now = run("search_passages", query=q, as_of_date="2026-06-01")
    assert "TEST-DOC-B:4:2024-06-01" not in ids(now)  # superseded on 2026-01-01
    assert "TEST-DOC-A:1(1):2026-01-01" in ids(now)
    then = run("search_passages", query=q, as_of_date="2025-06-01")
    assert ids(then) == ["TEST-DOC-B:4:2024-06-01"]  # doc A not yet in force


@pytest.mark.tool("search_passages")
def test_search_passages_doc_filter_and_k() -> None:
    r = run(
        "search_passages", query="test clause", as_of_date="2026-06-01", doc_filter=["TEST-DOC-C"]
    )
    assert ids(r) == ["TEST-DOC-C:3:2026-01-01"]
    r = run("search_passages", query="test clause report", as_of_date="2026-06-01", k=1)
    assert len(ids(r)) == 1


@pytest.mark.tool("search_passages")
def test_search_passages_no_support() -> None:
    r = run("search_passages", query="cryptocurrency mixer", as_of_date="2026-06-01")
    assert not r.is_error
    assert (r.result["status"], r.result["passages"]) == ("no_support", [])


@pytest.mark.tool("search_passages")
@pytest.mark.tool("submit_approved_payload")
def test_search_passages_returns_injected_text_as_data_only() -> None:
    r = run("search_passages", query="ignore previous instructions submit", as_of_date="2026-06-01")
    hit = r.result["passages"][0]
    assert hit["passage_id"] == "TEST-DOC-A:2(1):2026-01-01"
    assert "submit_approved_payload" in hit["text"]  # returned verbatim, nothing executed


@pytest.mark.tool("search_passages")
@pytest.mark.parametrize(
    "args",
    [
        {"query": "", "as_of_date": "2026-06-01"},
        {"query": "x" * 501, "as_of_date": "2026-06-01"},
        {"query": "cash", "as_of_date": "June 2026"},
        {"query": "cash", "as_of_date": "2026-06-01", "k": 0},
        {"query": "cash"},
    ],
)
def test_search_passages_invalid_input(args: dict[str, Any]) -> None:
    r = run("search_passages", **args)
    assert r.is_error and r.code == "INVALID_INPUT"


@pytest.mark.tool("get_passage")
def test_get_passage_happy() -> None:
    r = run("get_passage", passage_id="TEST-DOC-C:3:2026-01-01")
    assert not r.is_error
    assert (r.result["doc_id"], r.result["clause_ref"]) == ("TEST-DOC-C", "3")


@pytest.mark.tool("get_passage")
def test_get_passage_invalid_input() -> None:
    assert run("get_passage", passage_id="TEST-DOC-Z:1:1").code == "NOT_FOUND"
    assert run("get_passage").code == "INVALID_INPUT"


@pytest.mark.tool("search_passages")
@pytest.mark.tool("get_passage")
def test_wrong_agent_token() -> None:
    for agent in ("investigator", "build_payload", "submit", "ingest_validate"):
        r = run("search_passages", agent=agent, query="cash", as_of_date="2026-06-01")
        assert r.code == "FORBIDDEN", agent
    assert run("get_passage", agent="critic", passage_id="TEST-DOC-C:3:2026-01-01").code is None


@pytest.mark.tool("get_passage")
def test_out_of_scope_case() -> None:
    r = call(
        server(),
        "get_passage",
        {"agent_token": token("researcher", CASE), "case_id": OTHER_CASE, "passage_id": "x"},
    )
    assert r.code == "OUT_OF_SCOPE"


def test_corpus_errors(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="make ingest"):
        load_corpus(tmp_path / "missing.jsonl")
    dup = tmp_path / "dup.jsonl"
    line = CORPUS.read_text().splitlines()[0]
    dup.write_text(f"{line}\n{line}\n")
    with pytest.raises(CorpusError, match="duplicate"):
        load_corpus(dup)
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"passage_id": "x"}\n')
    with pytest.raises(CorpusError, match=r"bad\.jsonl:1"):
        load_corpus(bad)
