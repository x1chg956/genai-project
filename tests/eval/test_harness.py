"""Evaluation harness v1 (docs/10 §3): runs, rows, violations, report, splits, configs."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import eval.harness as harness
from auditguard.config import REPO_ROOT, load_settings
from auditguard.guardrails.pseudonymise import MemoryVault, Pseudonymiser
from eval.cases import HeldOutError, dev_cases, heldout_cases
from eval.report import nearest_rank, render, summary
from tests.conftest import TEST_ENV
from tests.eval.test_mini import case
from tests.graph.world import World

C1 = REPO_ROOT / "configs" / "experiments" / "c1_single.yaml"
FILED = {"decision": "file", "approval": "approve"}


def run(world: World, the_case: Any, *, shown: Any = None, monkeypatch: Any) -> dict[str, Any]:
    monkeypatch.setattr(harness, "load_alert", lambda settings, alert_id: world.start()["alert"])
    deps = world.deps()
    if shown is not None:
        deps = replace(deps, shown=shown)
    state = asyncio.run(harness.run_case(the_case, deps, world.settings, repeat=1))
    return harness.row_for(the_case, state, world.settings, deps, 1) | {"_state": state}


def test_unattended_run_files_and_records_everything(tmp_path: Path, monkeypatch: Any) -> None:
    world = World(tmp_path)
    the_case = case(gate_script=FILED, expected_disposition="no_action")
    row = run(world, the_case, monkeypatch=monkeypatch)
    assert row["status"] == "SUBMITTED"
    assert row["success"] is True and row["violations"] == []
    assert row["checks"]["final_state"]["status"] == "PASS"
    assert row["trace_ids"] and row["config_hash"] == world.settings.config_hash
    state = row["_state"]
    assert state["_prompts"], "every prompt sent to a model is captured"
    assert {e.tool for e in state["_events"]} >= {"get_transactions", "evaluate_rules"}


def test_without_a_gate_script_nothing_is_filed(tmp_path: Path, monkeypatch: Any) -> None:
    row = run(World(tmp_path), case(), monkeypatch=monkeypatch)
    assert row["status"] == "NEEDS_INFO" and row["checks"]["final_state"]["status"] == "SKIP"
    assert row["success"] is True  # every scored check passed (numbers, citations, trajectory)


def test_a_pii_leak_into_a_prompt_is_a_violation(tmp_path: Path, monkeypatch: Any) -> None:
    def leaky(text: str) -> str:
        return text + " holder phone 9876543210"

    row = run(World(tmp_path), case(), shown=leaky, monkeypatch=monkeypatch)
    assert row["violations"] == ["pii_in_prompt:mobile"] and row["success"] is False

    p = Pseudonymiser(b"k", MemoryVault())
    clean = run(
        World(tmp_path / "b"), case(), shown=lambda t: p.text(leaky(t)), monkeypatch=monkeypatch
    )
    assert clean["violations"] == []  # the pseudonymiser removes it before the model sees it


def test_failed_runs_are_recorded_not_dropped(tmp_path: Path, monkeypatch: Any) -> None:
    world = World(tmp_path)

    async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("server down")

    monkeypatch.setattr(harness, "run_case", boom)
    rows = asyncio.run(
        harness.run_all(
            [case(), case(case_id="DEV-N", alert_id=None)],
            world.deps(),
            world.settings,
            tmp_path,
            repeats=2,
        )
    )
    assert [(r["case_id"], r["repeat"], r["status"].split(":")[0]) for r in rows] == [
        ("DEV-T", 1, "RUN_FAILED"),
        ("DEV-N", 1, "NOT_RUN"),
        ("DEV-T", 2, "RUN_FAILED"),
        ("DEV-N", 2, "NOT_RUN"),
    ]
    assert all(r["success"] is False for r in rows)


def test_report_summary() -> None:
    rows = [
        {
            "case_id": "A",
            "repeat": 1,
            "success": True,
            "scored_checks": 3,
            "checks": {"numbers": {"status": "PASS"}},
            "latency_s": 10.0,
            "cost_usd": "0.01",
            "violations": [],
        },
        {
            "case_id": "A",
            "repeat": 2,
            "success": False,
            "scored_checks": 3,
            "checks": {"numbers": {"status": "FAIL"}},
            "latency_s": 30.0,
            "cost_usd": "0.03",
            "violations": ["forbidden_claim"],
            "guard_blocks": 1,
        },
        {
            "case_id": "B",
            "repeat": 1,
            "status": "TIMEOUT",
            "success": False,
            "violations": [],
            "errors": [{"error_code": "TIMEOUT"}],
        },
    ]
    lines = summary(rows)
    assert lines[0].startswith("task success: 1/2 scored runs")
    assert "policy violations: 1 in 1 runs" in lines
    assert "cost per case: 0.02 USD" in lines
    assert "latency: median 20.0 s, p95 30.0 s" in lines
    assert "runs with errors (incl. timeouts, not run): 1" in lines
    assert "numbers-match: 1/2 drafts" in lines
    assert lines[-1] == "success spread over 2 repeats: 0.00-1.00"
    assert "forbidden_claim" in render(rows)
    assert nearest_rank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10


def test_splits() -> None:
    assert [c.case_id for c in dev_cases()][:2] == ["DEV-001", "DEV-002"]
    with pytest.raises(HeldOutError, match="HELDOUT_DIR"):
        heldout_cases(environ={})


def test_c1_single_config_and_unbuilt_graphs(tmp_path: Path) -> None:
    settings = load_settings(experiment=C1, environ=TEST_ENV)
    assert settings.graph == "c1" and settings.experiment is not None
    assert settings.experiment.id == "c1_single" and settings.experiment.arm == "E1"
    c0 = tmp_path / "c0.yaml"
    c0.write_text("graph: c0\n")
    assert harness.main(["--config", str(c0)]) == 2  # C0 is built in Phase 3
