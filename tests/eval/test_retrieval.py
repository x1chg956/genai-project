"""E5 staircase runner (eval/retrieval.py) on the synthetic hybrid fixture with offline fakes.

The queries here are test fixtures over synthetic text, written for this test only; the
real 40 labelled queries are role 1's (eval/retrieval/queries.jsonl).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from eval.cases import HeldOutError
from eval.retrieval import ARMS, Query, load_queries, render, run_staircase, score_arm
from mcp_servers.regulatory_kb.hybrid import HybridRetriever
from tests.mcp_servers.regulatory_kb.test_hybrid import CORPUS, OverlapReranker, switches, vectors

QUERIES = [
    Query(
        qid="T1",
        type="procedure",
        query="furnishes the suspicious transaction report working days",
        as_of=date(2026, 6, 1),
        relevant=["SYN-REG-1:4(1):2026-01-01"],
    ),
    Query(
        qid="T2",
        type="definition",
        query="beneficial owner natural person",
        as_of=date(2026, 6, 1),
        relevant=["SYN-REG-2:2(b):2025-06-01"],
    ),
]


def factory(flags: dict[str, bool]) -> HybridRetriever:
    sw = switches(**flags)
    return HybridRetriever(
        CORPUS,
        sw,
        vectors=vectors(),
        reranker=OverlapReranker(),
        decomposer=lambda q: [q],
    )


def test_staircase_scores_every_arm_exactly() -> None:
    results = run_staircase(factory, QUERIES, k_final=5, score_floor=Decimal("0.2"))
    assert [r.arm for r in results] == list(ARMS)
    for r in results:
        assert (r.queries, r.recall_at_5, r.mrr, r.in_force_first) == (
            2,
            Decimal("1.0000"),
            Decimal("1.0000"),
            Decimal("1.0000"),
        )
    assert "vector_only | 2 | 1.0000" in render(results)


def test_a_miss_scores_zero_and_halves_the_mean() -> None:
    miss = Query(qid="T3", type="typology", query="zzz", as_of=date(2026, 6, 1), relevant=["NOPE"])
    r = score_arm(
        "bm25", factory(ARMS["bm25_rrf"]), [QUERIES[0], miss], k_final=5, score_floor=Decimal("0.2")
    )
    assert (r.recall_at_5, r.mrr) == (Decimal("0.5000"), Decimal("0.5000"))


def test_queries_loader_refuses_heldout_and_bad_rows(tmp_path: Path) -> None:
    with pytest.raises(HeldOutError):
        load_queries(tmp_path / "h" / "q.jsonl", environ={"HELDOUT_DIR": str(tmp_path / "h")})
    bad = tmp_path / "q.jsonl"
    bad.write_text(
        '{"qid": "Q1", "type": "guess", "query": "x", "as_of": "2026-01-01", "relevant": []}\n'
    )
    with pytest.raises(ValueError):
        load_queries(bad, environ={})


def test_runner_explains_missing_queries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from eval.retrieval import main

    assert main(["--queries", str(tmp_path / "none.jsonl")]) == 2
    assert "role 1" in capsys.readouterr().out
