"""The evidence generators: kinds match docs/05, every tool has tests, graphs export."""

from __future__ import annotations

from types import SimpleNamespace

from auditguard.config import REPO_ROOT
from scripts import export_graphs, gen_tool_catalogue

PHASE1 = {
    "get_transactions": "R",
    "get_account_summary": "R",
    "get_counterparty_flows": "R",
    "evaluate_rules": "C",
    "get_rule_metadata": "C",
    "search_passages": "R",
    "get_passage": "R",
    "prepare_payload": "W",
    "submit_approved_payload": "W",
    "get_receipt": "R",
}


def test_spec_table_gives_every_phase1_tool_its_kind() -> None:
    kinds = gen_tool_catalogue.spec_kinds(gen_tool_catalogue.SPEC.read_text(encoding="utf-8"))
    assert {k: kinds[k] for k in PHASE1} == PHASE1


def test_tool_kind_needs_exactly_one_tag() -> None:
    def tool(*tags: str) -> SimpleNamespace:
        return SimpleNamespace(meta={"fastmcp": {"tags": list(tags)}})

    assert gen_tool_catalogue.tool_kind(tool("write")) == "W"
    assert gen_tool_catalogue.tool_kind(tool()) is None
    assert gen_tool_catalogue.tool_kind(tool("read", "write")) is None


def test_every_phase1_tool_has_a_marked_test() -> None:
    tests = gen_tool_catalogue.marked_tests()
    assert all(tests.get(name) for name in PHASE1), sorted(set(PHASE1) - set(tests))


def test_catalogue_generates_cleanly() -> None:
    assert gen_tool_catalogue.main() == 0
    text = (REPO_ROOT / "docs" / "tool_catalogue.md").read_text(encoding="utf-8")
    assert all(f"`{name}`" in text for name in PHASE1)


def test_c1_graph_export_has_every_node() -> None:
    assert export_graphs.main() == 0
    text = (REPO_ROOT / "docs" / "graph_c1.md").read_text(encoding="utf-8")
    for node in (
        "ingest_validate",
        "single_agent",
        "decision_gate",
        "build_payload",
        "approval_gate",
        "submit",
        "close_no_file",
        "needs_info",
    ):
        assert f"{node}({node})" in text
