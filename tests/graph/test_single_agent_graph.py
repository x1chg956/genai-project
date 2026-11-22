"""C1 graph end to end on in-memory servers: gates, approval binding, resume (docs/02 Phase 1)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from auditguard.approval.canonical import canonical_json
from auditguard.graph.single_agent import compile_graph, serializer, sqlite_checkpointer
from tests.graph.world import ANALYST, OFFICER, World
from tests.mcp_servers.helpers import CASE
from tests.rules.helpers import pack

CFG = {"configurable": {"thread_id": "t1"}}
FILE = {"decision": "file", "decided_by": ANALYST}
APPROVE = {"action": "approve", "approver_id": OFFICER}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def pending(out: dict[str, Any]) -> dict[str, Any]:
    interrupts = out.get("__interrupt__")
    assert interrupts, f"expected an interrupt, got status {out.get('status')}"
    return dict(interrupts[0].value)


async def drive(
    world: World, *resumes: Any, interrupt_before: list[str] | None = None, saver: Any = None
) -> tuple[Any, list[dict[str, Any]]]:
    app = compile_graph(
        world.deps(), saver or InMemorySaver(serde=serializer()), interrupt_before=interrupt_before
    )
    seen = []
    out = await app.ainvoke(world.start(), CFG)
    for value in resumes:
        if out.get("__interrupt__"):
            seen.append(pending(out))
        out = await app.ainvoke(Command(resume=value), CFG)
    if out.get("__interrupt__"):
        seen.append(pending(out))
    return out, seen


def test_happy_path_reaches_a_receipt(tmp_path: Path) -> None:
    world = World(tmp_path)
    out, seen = run(drive(world, FILE, APPROVE))
    assert [s["kind"] for s in seen] == ["decision", "approval"]
    assert out["status"] == "SUBMITTED"
    receipt, payload = out["receipt"], out["payload"]
    assert receipt.case_id == CASE and receipt.payload_sha256 == payload.sha256
    assert seen[1]["sha256"] == payload.sha256 and seen[1]["payload"] == payload.canonical_json


def test_indicators_come_from_the_tool_not_the_model(tmp_path: Path) -> None:
    out, _ = run(drive(World(tmp_path)))
    result = out["result"]
    assert {r.rule_id for r in result.indicators} == set(pack().rules)
    assert out["agent_stats"]["tool_calls"] == 3


def test_payload_is_canonical_and_holds_no_model_or_scores(tmp_path: Path) -> None:
    _, seen = run(drive(World(tmp_path), FILE))
    body = json.loads(seen[-1]["payload"])
    assert canonical_json(body).decode() == seen[-1]["payload"]
    text = seen[-1]["payload"].lower()
    assert "qwen" not in text and "score" not in text
    assert body["decided_by"] == ANALYST and body["report_details"]["report_version"] == 1


def test_no_file_closes_the_case(tmp_path: Path) -> None:
    decision = {
        "decision": "no_file",
        "reason_code": "BENIGN_PAYROLL",
        "rationale": "salary credits",
        "decided_by": ANALYST,
    }
    out, _ = run(drive(World(tmp_path), decision))
    assert out["status"] == "CLOSED_NO_FILE" and out.get("payload") is None


def test_only_the_analyst_can_decide(tmp_path: Path) -> None:
    _, seen = run(drive(World(tmp_path), {"decision": "file", "decided_by": OFFICER}))
    assert seen[-1]["kind"] == "decision" and "only the case's analyst" in seen[-1]["notice"]


def test_reject_ends_without_a_write(tmp_path: Path) -> None:
    world = World(tmp_path)
    out, _ = run(drive(world, FILE, {"action": "reject", "approver_id": OFFICER}))
    assert out["status"] == "REJECTED" and out.get("receipt") is None


def test_self_approval_is_refused_at_the_gate(tmp_path: Path) -> None:
    out, seen = run(drive(World(tmp_path), FILE, {"action": "approve", "approver_id": ANALYST}))
    assert seen[-1]["kind"] == "approval" and "self-approval" in seen[-1]["notice"]
    assert out.get("approval") is None


def test_edit_makes_a_new_version_and_needs_a_fresh_approval(tmp_path: Path) -> None:
    world = World(tmp_path)

    async def scenario() -> tuple[Any, list[dict[str, Any]]]:
        app = compile_graph(world.deps(), InMemorySaver(serde=serializer()))
        out = await app.ainvoke(world.start(), CFG)
        out = await app.ainvoke(Command(resume=FILE), CFG)
        first = pending(out)
        draft = out["drafts"][-1].model_dump(mode="json")
        draft["sections"]["close"] = [
            {"text": "Referred to the Principal Officer.", "material": False}
        ]
        out = await app.ainvoke(Command(resume={"action": "edit", "edited_draft": draft}), CFG)
        second = pending(out)
        out = await app.ainvoke(Command(resume=APPROVE), CFG)
        return out, [first, second]

    out, (first, second) = run(scenario())
    assert second["report_version"] == 2 and second["sha256"] != first["sha256"]
    assert "Referred to the Principal Officer." in second["diff"]
    assert out["status"] == "SUBMITTED" and out["receipt"].payload_sha256 == second["sha256"]


def test_edit_after_approval_gives_stale_hash(tmp_path: Path) -> None:
    """Approve, then change the portal's stored draft before submit: the portal refuses."""
    world = World(tmp_path)

    async def scenario() -> Any:
        app = compile_graph(
            world.deps(), InMemorySaver(serde=serializer()), interrupt_before=["submit"]
        )
        await app.ainvoke(world.start(), CFG)
        await app.ainvoke(Command(resume=FILE), CFG)
        out = await app.ainvoke(Command(resume=APPROVE), CFG)  # paused before submit
        assert out["approval"] is not None
        body = json.loads(out["payload"].canonical_json)
        body["grounds_of_suspicion"] += " Edited after approval."
        from tests.mcp_servers.helpers import acall, token

        async with world.client("filing_portal") as portal:
            edit = await acall(
                portal,
                "prepare_payload",
                {
                    "agent_token": token("build_payload"),
                    "case_id": CASE,
                    "case_model_version": 2,
                    "payload": body,
                },
            )
            assert not edit.is_error
        return await app.ainvoke(None, CFG)

    out = run(scenario())
    assert out["status"] == "ERROR" and out.get("receipt") is None
    assert out["errors"][-1].error_code == "STALE_HASH"


def test_kill_at_the_decision_gate_and_resume_without_re_ingest(tmp_path: Path) -> None:
    world = World(tmp_path)

    async def first_process() -> dict[str, Any]:
        async with sqlite_checkpointer(world.settings) as saver:
            app = compile_graph(world.deps(), saver)
            return dict(await app.ainvoke(world.start(), CFG))

    async def second_process() -> dict[str, Any]:
        async with sqlite_checkpointer(world.settings) as saver:
            app = compile_graph(world.deps(), saver)
            snapshot = await app.aget_state(CFG)
            assert [t.name for t in snapshot.tasks] == ["decision_gate"]
            out = await app.ainvoke(Command(resume=FILE), CFG)
            return dict(await app.ainvoke(Command(resume=APPROVE), CFG)) if out else {}

    assert pending(run(first_process()))["kind"] == "decision"
    reads_before = world.repo.reads
    agent_turns = world.answer_calls
    out = run(second_process())
    assert out["status"] == "SUBMITTED"
    assert world.repo.reads == reads_before, "resume must not re-read the ledger"
    assert world.answer_calls == agent_turns, "resume must not re-run the agent"


@pytest.mark.parametrize("node", ["decision_gate", "approval_gate"])
def test_gates_only_read_before_interrupt(tmp_path: Path, node: str) -> None:
    """Re-running a gate from its start (what resume does) writes nothing new."""
    world = World(tmp_path)
    resumes = [] if node == "decision_gate" else [FILE]
    run(drive(world, *resumes))
    with world.approvals._connect() as conn:
        assert conn.execute("select count(*) from approvals").fetchone()[0] == 0


def test_decision_gate_shows_the_recommendation_and_tool_records(tmp_path: Path) -> None:
    out, seen = run(drive(World(tmp_path)))
    assert seen[-1]["evidence_pack"]["recommendation"] == "no_action"
    assert [r["tool"] for r in out["tool_records"]] == [
        "evaluate_rules",
        "get_account_summary",
        "search_passages",
    ]
