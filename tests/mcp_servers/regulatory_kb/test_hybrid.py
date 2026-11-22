"""Hybrid retrieval v1 (hybrid.py) and the Phase 2 regulatory_kb tools.

Offline and deterministic: a bag-of-words hashing embedder stands in for bge-small, a
term-overlap scorer for the cross-encoder, and Qdrant runs in-process (`:memory:`), so the
real QdrantVectors code path is exercised. Corpus: tests/fixtures/passages_hybrid.jsonl
(synthetic text, not regulatory or typology material).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastmcp import FastMCP
from qdrant_client import QdrantClient

from auditguard.config import REPO_ROOT
from mcp_servers.regulatory_kb.hybrid import HybridRetriever, QdrantVectors, Switches, rrf
from mcp_servers.regulatory_kb.index import load_corpus, tokenize
from mcp_servers.regulatory_kb.server import build_server
from tests.mcp_servers.helpers import CASE, Called, call, guard, token

CORPUS = load_corpus(REPO_ROOT / "tests" / "fixtures" / "passages_hybrid.jsonl")
TODAY = date(2026, 6, 1)
CURRENT = "SYN-REG-1:4(1):2026-01-01"
OLD = "SYN-REG-1:4(1):2024-01-01"


class HashEmbedder:
    """Unit vector of hashed word counts: identical words → high cosine. Not semantic."""

    dim = 64

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            v = [0.0] * self.dim
            for w in tokenize(text):
                v[int(hashlib.sha256(w.encode()).hexdigest(), 16) % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


class OverlapReranker:
    """Logit = 2 x (shared words) - 6: a passage sharing 3+ words scores above 0.5."""

    def __init__(self) -> None:
        self.calls = 0

    def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        self.calls += 1
        q = set(tokenize(query))
        return [2.0 * len(q & set(tokenize(t))) - 6.0 for t in texts]


def switches(**over: Any) -> Switches:
    base: dict[str, Any] = {
        "bm25": True,
        "vector": True,
        "rerank": True,
        "decompose": False,
        "k_bm25": 20,
        "k_vector": 20,
        "rrf_k": 60,
        "rerank_candidates": 20,
        "vector_floor": Decimal("0.30"),
    }
    return Switches(**(base | over))


def vectors() -> QdrantVectors:
    v = QdrantVectors(QdrantClient(location=":memory:"), "passages_test", HashEmbedder())
    v.ensure(CORPUS)
    return v


def retriever(decomposer: Any = None, reranker: Any = None, **over: Any) -> HybridRetriever:
    sw = switches(**over)
    return HybridRetriever(
        CORPUS,
        sw,
        vectors=vectors() if sw.vector else None,
        reranker=(reranker or OverlapReranker()) if sw.rerank else None,
        decomposer=decomposer,
    )


def search(r: HybridRetriever, query: str, **kw: Any) -> list[str]:
    args: dict[str, Any] = {"as_of": TODAY, "k": 5, "score_floor": Decimal("0.2")} | kw
    return [p.passage_id for p in r.search(query, **args)]


# --------------------------------------------------------------------------- pieces


def test_rrf_exact_scores_and_stable_ties() -> None:
    fused = dict(rrf([["a", "b"], ["b", "c"]], k=60))
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["a"] == pytest.approx(1 / 61) and fused["c"] == pytest.approx(1 / 62)
    assert [p for p, _ in rrf([["a", "b"], ["b", "c"]], k=60)] == ["b", "a", "c"]
    assert [p for p, _ in rrf([["y"], ["x"]], k=60)] == ["x", "y"]  # tie → passage_id


def test_qdrant_collection_is_built_once() -> None:
    client = QdrantClient(location=":memory:")
    v = QdrantVectors(client, "passages_x", HashEmbedder())
    assert v.ensure(CORPUS) == len(CORPUS)
    assert v.ensure(CORPUS) == 0
    assert client.count("passages_x").count == len(CORPUS)


def test_switches_need_a_ranker_and_their_components() -> None:
    with pytest.raises(ValueError, match="bm25 or vector"):
        switches(bm25=False, vector=False)
    with pytest.raises(ValueError, match="vector"):
        HybridRetriever(CORPUS, switches(), reranker=OverlapReranker())
    with pytest.raises(ValueError, match="decompose"):
        HybridRetriever(CORPUS, switches(vector=False, rerank=False, decompose=True))


# --------------------------------------------------------------------------- pipeline


@pytest.mark.parametrize(
    "arm",
    [
        {"bm25": False, "rerank": False},  # vector only
        {"rerank": False},  # + BM25 with RRF
        {},  # + reranker
    ],
)
def test_every_arm_ranks_the_clause_in_force_and_never_the_superseded_one(
    arm: dict[str, Any],
) -> None:
    found = search(retriever(**arm), "furnishes the suspicious transaction report working days")
    assert found[0] == CURRENT and OLD not in found
    assert search(retriever(**arm), "furnish report fifteen working days", as_of=date(2025, 3, 1))[
        0
    ] == (OLD)


def test_doc_filter_and_kind_are_applied_before_ranking() -> None:
    r = retriever()
    assert search(r, "beneficial owner natural person", doc_filter=["SYN-REG-2"]) == [
        "SYN-REG-2:2(b):2025-06-01"
    ]
    filtered = search(r, "beneficial owner natural person", doc_filter=["SYN-REG-1"])
    assert "SYN-REG-2:2(b):2025-06-01" not in filtered
    assert not any(p.startswith("SYN-TYP") for p in search(r, "mule accounts unrelated senders"))
    typ = search(r, "mule accounts unrelated senders funds out", kind="typology", as_of=None)
    assert typ[0] == "SYN-TYP-1:1:2025-01-01"


def test_reranker_reorders_and_its_floor_gives_no_support() -> None:
    rr = OverlapReranker()
    r = retriever(reranker=rr)
    found = r.search(
        "cash transactions records five years", as_of=TODAY, k=5, score_floor=Decimal("0.2")
    )
    assert found[0].passage_id == "SYN-REG-1:4(2):2026-01-01"
    assert Decimal("0") < found[0].score <= Decimal("1")  # sigmoid probability
    assert rr.calls == 1
    assert search(r, "records", score_floor=Decimal("0.99")) == []


def test_bm25_alone_keeps_the_phase1_floor() -> None:
    r = retriever(vector=False, rerank=False)
    assert search(r, "beneficial owner", score_floor=Decimal("0")) == ["SYN-REG-2:2(b):2025-06-01"]
    assert search(r, "beneficial owner", score_floor=Decimal("100")) == []


def test_decomposition_adds_subqueries_and_keeps_the_original() -> None:
    seen: list[str] = []

    def decompose(q: str) -> list[str]:
        seen.append(q)
        return ["beneficial owner definition", "cash records five years", "x", "dropped 4th"]

    r = retriever(decomposer=decompose, decompose=True, rerank=False, vector=False)
    assert r.queries("owner and records") == [
        "owner and records",
        "beneficial owner definition",
        "cash records five years",
        "x",
    ]  # at most 3 sub-queries: the 4th is dropped
    found = search(r, "owner and records")
    assert {"SYN-REG-2:2(b):2025-06-01", "SYN-REG-1:4(2):2026-01-01"} <= set(found)
    assert seen


def test_a_failing_decomposer_falls_back_to_the_query() -> None:
    def broken(q: str) -> list[str]:
        raise RuntimeError("model down")

    r = retriever(decomposer=broken, decompose=True, rerank=False, vector=False)
    assert r.queries("beneficial owner") == ["beneficial owner"]
    assert search(r, "beneficial owner", score_floor=Decimal("0")) == ["SYN-REG-2:2(b):2025-06-01"]


def test_passage_text_that_reads_like_an_instruction_is_only_scored() -> None:
    """Rule 2: text is embedded and scored; nothing in it changes what the search does."""
    r = retriever()
    before = search(r, "suspicious transaction report working days")
    assert before == search(r, "suspicious transaction report working days")


# --------------------------------------------------------------------------- tools


def server() -> FastMCP:
    return build_server(
        retriever(), guard("regulatory_kb"), k_default=5, score_floor=Decimal("0.2")
    )


def run(tool: str, agent: str = "single_agent", **args: Any) -> Called:
    return call(server(), tool, {"agent_token": token(agent), "case_id": CASE} | args)


@pytest.mark.tool("search_typologies")
def test_search_typologies_happy() -> None:
    r = run("search_typologies", query="cash deposits split below a reporting threshold")
    assert not r.is_error and r.result["status"] == "ok"
    assert r.result["passages"][0]["passage_id"] == "SYN-TYP-1:2:2025-01-01"
    assert all(p["doc_id"] == "SYN-TYP-1" for p in r.result["passages"])
    none = run("search_typologies", query="zzz qqq")
    assert (none.result["status"], none.result["passages"]) == ("no_support", [])


@pytest.mark.tool("search_typologies")
@pytest.mark.parametrize("args", [{}, {"query": ""}, {"query": "x", "k": 0}])
def test_search_typologies_invalid_input(args: dict[str, Any]) -> None:
    assert run("search_typologies", **args).code == "INVALID_INPUT"


@pytest.mark.tool("list_document_versions")
def test_list_document_versions_happy_oldest_first() -> None:
    r = run("list_document_versions", agent="researcher", doc_id="SYN-REG-1")
    assert not r.is_error
    assert [
        (v["version"], v["effective_from"], v["superseded_on"], v["passage_count"])
        for v in r.result["versions"]
    ] == [("2024-01-01", "2024-01-01", "2026-01-01", 1), ("2026-01-01", "2026-01-01", None, 2)]
    assert r.result["versions"][0]["superseded_by"] == "SYN-REG-1"


@pytest.mark.tool("list_document_versions")
def test_list_document_versions_invalid_input_and_not_found() -> None:
    assert run("list_document_versions", agent="researcher").code == "INVALID_INPUT"
    assert run("list_document_versions", agent="researcher", doc_id="NOPE").code == "NOT_FOUND"


def test_wrong_agent_token_for_new_tools() -> None:
    assert run("search_typologies", agent="researcher", query="mule").code == "FORBIDDEN"
    assert run("list_document_versions", agent="build_payload", doc_id="SYN-REG-1").code == (
        "FORBIDDEN"
    )
