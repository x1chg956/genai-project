"""E5 retrieval staircase (docs/07): Recall@5, MRR and in-force-first for each arm.

Queries: eval/retrieval/queries.jsonl, written and labelled by role 1 (40 queries, four
types). This tool never writes or guesses labels. Arms, each adding one step:

| arm | bm25 | vector | rerank | decompose |
|---|---|---|---|---|
| vector_only | off | on | off | off |
| bm25_rrf | on | on | off | off |
| rerank | on | on | on | off |
| decompose | on | on | on | on |

Metrics per arm: Recall@5 (share of a query's relevant passages in the top 5, averaged),
MRR (1 / rank of the first relevant passage in the top k_final x 2, else 0) and in-force-first
(of queries whose results include a passage from a relevant document, the share whose first
such passage is in force on the query's as_of date). `--min-recall` makes it a gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from auditguard.config import REPO_ROOT, Settings, load_settings, resolve_path
from eval.cases import check_not_heldout
from mcp_servers.regulatory_kb.hybrid import HybridRetriever, Switches
from mcp_servers.regulatory_kb.models import CorpusPassage

QUERIES = REPO_ROOT / "eval" / "retrieval" / "queries.jsonl"
ARMS: dict[str, dict[str, bool]] = {
    "vector_only": {"bm25": False, "vector": True, "rerank": False, "decompose": False},
    "bm25_rrf": {"bm25": True, "vector": True, "rerank": False, "decompose": False},
    "rerank": {"bm25": True, "vector": True, "rerank": True, "decompose": False},
    "decompose": {"bm25": True, "vector": True, "rerank": True, "decompose": True},
}
FOUR = Decimal("0.0001")


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qid: str
    type: Literal["rule_provenance", "typology", "procedure", "definition"]
    query: str
    as_of: date
    relevant: list[str]


@dataclass(frozen=True)
class ArmResult:
    arm: str
    queries: int
    recall_at_5: Decimal
    mrr: Decimal
    in_force_first: Decimal | None


def load_queries(path: Path, environ: dict[str, str] | None = None) -> list[Query]:
    check_not_heldout(path, environ)
    rows = [Query.model_validate_json(x) for x in path.read_text("utf-8").splitlines() if x]
    if len({q.qid for q in rows}) != len(rows):
        raise ValueError(f"{path.name}: duplicate qid")
    return rows


def _ratio(num: Decimal, den: int) -> Decimal:
    return (num / den).quantize(FOUR, rounding=ROUND_HALF_EVEN) if den else Decimal(0)


def score_arm(
    arm: str,
    retriever: HybridRetriever,
    queries: Sequence[Query],
    *,
    k_final: int,
    score_floor: Decimal,
) -> ArmResult:
    recall, rr, first_ok, first_seen = Decimal(0), Decimal(0), 0, 0
    depth = max(5, k_final * 2)
    for q in queries:
        found = retriever.search(q.query, as_of=q.as_of, k=depth, score_floor=score_floor)
        ids = [p.passage_id for p in found]
        relevant = set(q.relevant)
        if relevant:
            recall += Decimal(len(relevant & set(ids[:5]))) / len(relevant)
        rank = next((i for i, pid in enumerate(ids, start=1) if pid in relevant), None)
        if rank:
            rr += Decimal(1) / rank
        docs = {retriever.by_id[r].doc_id for r in relevant if r in retriever.by_id}
        first = next(
            (retriever.by_id[pid] for pid in ids if retriever.by_id[pid].doc_id in docs), None
        )
        if first is not None:
            first_seen += 1
            first_ok += first.in_force(q.as_of)
    return ArmResult(
        arm=arm,
        queries=len(queries),
        recall_at_5=_ratio(recall, len(queries)),
        mrr=_ratio(rr, len(queries)),
        in_force_first=_ratio(Decimal(first_ok), first_seen) if first_seen else None,
    )


Factory = Callable[[dict[str, bool]], HybridRetriever]


def run_staircase(
    factory: Factory,
    queries: Sequence[Query],
    *,
    k_final: int,
    score_floor: Decimal,
    arms: dict[str, dict[str, bool]] = ARMS,
) -> list[ArmResult]:
    return [
        score_arm(name, factory(flags), queries, k_final=k_final, score_floor=score_floor)
        for name, flags in arms.items()
    ]


def live_factory(settings: Settings) -> Factory:
    """Builds every component once (vectors, reranker, decomposer) and switches per arm."""
    from qdrant_client import QdrantClient

    from auditguard.retrieval.decompose import make_decomposer
    from mcp_servers.regulatory_kb.hybrid import FastEmbedder, FastReranker, QdrantVectors
    from mcp_servers.regulatory_kb.index import load_corpus
    from mcp_servers.regulatory_kb.server import corpus_version

    r = settings.retrieval
    path = resolve_path(settings.corpus.passages)
    passages: list[CorpusPassage] = load_corpus(path)
    cache = resolve_path(r.model_cache)
    url = settings.qdrant.url
    client = QdrantClient(location=":memory:") if url == ":memory:" else QdrantClient(url=url)
    vectors = QdrantVectors(
        client, f"passages_{corpus_version(path)}", FastEmbedder(r.embedding_model, cache)
    )
    vectors.ensure(passages)
    reranker = FastReranker(r.reranker_model, cache)
    decomposer = make_decomposer(settings)

    def build(flags: dict[str, bool]) -> HybridRetriever:
        sw = Switches(
            **flags,
            k_bm25=r.k_bm25,
            k_vector=r.k_vector,
            rrf_k=r.rrf_k,
            rerank_candidates=r.rerank_candidates,
            vector_floor=r.vector_floor,
        )
        return HybridRetriever(
            passages, sw, vectors=vectors, reranker=reranker, decomposer=decomposer
        )

    return build


def render(results: Sequence[ArmResult]) -> str:
    lines = ["arm | queries | Recall@5 | MRR | in-force-first", "--- | --- | --- | --- | ---"]
    for r in results:
        iff = "-" if r.in_force_first is None else str(r.in_force_first)
        lines.append(f"{r.arm} | {r.queries} | {r.recall_at_5} | {r.mrr} | {iff}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=QUERIES)
    parser.add_argument("--min-recall", type=Decimal, default=None)
    args = parser.parse_args(argv)
    if not args.queries.is_file():
        print(f"no labelled queries at {args.queries}: role 1 writes eval/retrieval/queries.jsonl")
        return 2
    settings = load_settings()
    queries = load_queries(args.queries)
    results = run_staircase(
        live_factory(settings),
        queries,
        k_final=settings.retrieval.k_final,
        score_floor=Decimal(str(settings.retrieval.score_floor)),
    )
    out = REPO_ROOT / "runs" / f"retrieval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    out.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "config_hash": settings.config_hash,
        "corpus": settings.corpus.passages,
        "queries": str(args.queries),
        "arms": [{k: str(v) for k, v in asdict(r).items()} for r in results],
    }
    (out / "results.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(render(results))
    print(f"\nresults: {out.relative_to(REPO_ROOT)}/results.json")
    if args.min_recall is not None:
        best = results[-1].recall_at_5
        if best < args.min_recall:
            print(f"gate: Recall@5 {best} < {args.min_recall}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
