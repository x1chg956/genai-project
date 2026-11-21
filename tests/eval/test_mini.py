"""Mini evaluation: case loading (never held-out), checkers, safe gate defaults."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from auditguard.config import REPO_ROOT
from auditguard.graph.single_agent import compile_graph, serializer
from eval.cases import EvalCase, HeldOutError, load_cases
from eval.checkers import run_checks
from eval.mini import gate_answer, table
from tests.graph.world import World

SCAFFOLD = REPO_ROOT / "eval" / "cases" / "dev" / "phase1.jsonl"
BUDGET = 20


def case(**over: Any) -> EvalCase:
    return EvalCase.model_validate(
        {
            "case_id": "DEV-T",
            "split": "dev",
            "scenario_family": "rapid_pass_through",
            "alert_id": "ALR-SYN-0001",
            **over,
        }
    )


def finished(world: World, *resumes: Any) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        app = compile_graph(world.deps(), InMemorySaver(serde=serializer()))
        cfg = {"configurable": {"thread_id": "e"}}
        await app.ainvoke(world.start(), cfg)
        for value in resumes:
            await app.ainvoke(Command(resume=value), cfg)
        return dict((await app.aget_state(cfg)).values)

    return asyncio.run(go())


def by_name(checks: list[Any]) -> dict[str, str]:
    return {c.name: c.status for c in checks}


def test_scaffold_has_six_cases_with_no_expected_outcomes() -> None:
    cases = load_cases(SCAFFOLD)
    assert [c.case_id for c in cases] == [f"DEV-00{i}" for i in range(1, 7)]
    for c in cases:
        assert c.expected_disposition is None and c.expected_rule_outputs is None
        assert c.gate_script is None and c.todo and c.todo.startswith("TODO(role1)")


def test_loader_refuses_heldout_paths_without_opening_them(tmp_path: Path) -> None:
    secret = tmp_path / "heldout" / "cases.jsonl"  # never created: refusal comes first
    with pytest.raises(HeldOutError):
        load_cases(secret, environ={"HELDOUT_DIR": str(tmp_path / "heldout")})
    with pytest.raises(HeldOutError):
        load_cases(REPO_ROOT / "eval" / "heldout" / "x.jsonl", environ={})


def test_loader_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text(
        json.dumps({"case_id": "X", "split": "dev", "scenario_family": "f", "expected_secret": 1})
        + "\n"
    )
    with pytest.raises(ValueError, match=r"c\.jsonl:1"):
        load_cases(path, environ={})


def test_unset_expectations_skip_and_invariants_still_run(tmp_path: Path) -> None:
    state = finished(World(tmp_path), {"decision": "needs_info", "decided_by": "ANL-SYN-0001"})
    checks = by_name(run_checks(case(), state, BUDGET))
    assert checks == {
        "disposition": "SKIP",
        "rules": "SKIP",
        "numbers": "PASS",
        "citations": "PASS",
        "trajectory": "PASS",
        "final_state": "SKIP",
    }


def test_filled_expectations_pass_and_fail(tmp_path: Path) -> None:
    state = finished(
        World(tmp_path),
        {"decision": "file", "decided_by": "ANL-SYN-0001"},
        {"action": "approve", "approver_id": "PO-SYN-0001"},
    )
    good = case(
        expected_disposition="no_action",
        expected_rule_outputs={"must_fire": [], "must_not_fire": ["R04"]},
        gate_script={"decision": "file", "approval": "approve"},
        expected_trajectory={"required_tool_classes": ["rules", "retrieval"]},
    )
    assert set(by_name(run_checks(good, state, BUDGET)).values()) == {"PASS"}
    bad = case(
        expected_disposition="escalate_for_review",
        expected_rule_outputs={"must_fire": ["R04"]},
        gate_script={"decision": "no_file", "reason_code": "X", "rationale": "y"},
        expected_trajectory={"required_tool_classes": ["drilldown"], "max_tool_calls": 2},
    )
    checks = by_name(run_checks(bad, state, BUDGET))
    assert checks["disposition"] == checks["rules"] == checks["final_state"] == "FAIL"
    assert checks["trajectory"] == "FAIL"


def test_numbers_checker_catches_a_number_no_tool_returned(tmp_path: Path) -> None:
    world = World(tmp_path)
    state = finished(world)
    draft = state["result"].draft.model_dump(mode="json")
    draft["sections"]["body"] = [{"text": "Moved INR 4,321.00.", "evidence_ids": ["R04"]}]
    from auditguard.schemas import DraftReport

    result = state["result"].model_copy(update={"draft": DraftReport.model_validate(draft)})
    checks = by_name(run_checks(case(), {**state, "result": result}, BUDGET))
    assert checks["numbers"] == "FAIL"


def test_no_gate_script_never_files() -> None:
    assert gate_answer({"kind": "decision"}, None)["decision"] == "needs_info"
    assert gate_answer({"kind": "approval"}, None)["action"] == "reject"


def test_table_reports_numbers_and_citation_match() -> None:
    rows = [
        {
            "case_id": "A",
            "checks": {
                n: {"status": "PASS"}
                for n in (
                    "disposition",
                    "rules",
                    "numbers",
                    "citations",
                    "trajectory",
                    "final_state",
                )
            },
        },
        {"case_id": "B", "status": "NOT_RUN: no alert yet (role 1)"},
    ]
    text = table(rows)
    assert "numbers-match: 1/1 drafts" in text and "NOT_RUN" in text
