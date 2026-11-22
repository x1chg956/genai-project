"""The single agent's code checks: IDs and numbers must come from tools; retry once."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from auditguard.graph.single_agent import compile_graph, serializer
from auditguard.schemas import SingleAgentAnswer
from tests.graph.world import World, good_answer
from tests.rules.helpers import pack

CFG = {"configurable": {"thread_id": "t"}}


def run_until_decision(world: World) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        app = compile_graph(world.deps(), InMemorySaver(serde=serializer()))
        return dict(await app.ainvoke(world.start(), CFG))

    return asyncio.run(go())


def with_body(answer: SingleAgentAnswer, text: str, ids: list[str]) -> SingleAgentAnswer:
    data = answer.model_dump(mode="json")
    data["draft"]["sections"]["body"] = [{"text": text, "evidence_ids": ids}]
    return SingleAgentAnswer.model_validate(data)


def test_invented_id_is_retried_once_then_accepted(tmp_path: Path) -> None:
    def answer(records: list[dict[str, Any]], attempt: int) -> SingleAgentAnswer:
        good = good_answer(records)
        return (
            with_body(good, "A transfer occurred.", ["TXN-SYN-9999999"]) if attempt == 1 else good
        )

    world = World(tmp_path, answer=answer)
    out = run_until_decision(world)
    assert out["result"] is not None
    assert out["agent_stats"]["structured_retries"] == 1
    assert world.answer_calls == 2


def test_number_not_from_a_tool_fails_after_one_retry(tmp_path: Path) -> None:
    def answer(records: list[dict[str, Any]], attempt: int) -> SingleAgentAnswer:
        good = good_answer(records)
        rule = good.draft.sections.grounds[0].evidence_ids
        return with_body(good, "The account received INR 12,345.67 in total.", rule)

    world = World(tmp_path, answer=answer)
    out = run_until_decision(world)
    assert out["result"] is None
    assert "12345.67" in out["errors"][-1].message
    assert world.answer_calls == 2
    view = out["__interrupt__"][0].value
    assert view["kind"] == "decision"  # the analyst still decides
    assert {r["rule_id"] for r in view["evidence_pack"]["indicators"]} == set(pack().rules)


def test_answer_needs_a_benign_hypothesis(tmp_path: Path) -> None:
    def answer(records: list[dict[str, Any]], attempt: int) -> SingleAgentAnswer:
        data = good_answer(records).model_dump(mode="json")
        data["hypotheses"] = [h for h in data["hypotheses"] if h["kind"] != "benign"]
        return SingleAgentAnswer.model_validate(data)

    out = run_until_decision(World(tmp_path, answer=answer))
    assert "benign" in out["errors"][-1].message


def test_tool_budget_stops_the_agent(tmp_path: Path, monkeypatch: Any) -> None:
    world = World(tmp_path)
    budget = world.settings.budgets.case.model_copy(update={"tool_calls": 1})
    budgets = world.settings.budgets.model_copy(update={"case": budget})
    world.settings = world.settings.model_copy(update={"budgets": budgets})
    out = run_until_decision(world)
    assert out["agent_stats"]["tool_calls"] == 1
    assert out["agent_stats"]["stopped_by"] == "tool_calls"


def test_step_limit_keeps_the_conversation_for_stats(tmp_path: Path) -> None:
    world = World(tmp_path)
    budget = world.settings.budgets.case.model_copy(update={"steps": 1})
    budgets = world.settings.budgets.model_copy(update={"case": budget})
    world.settings = world.settings.model_copy(update={"budgets": budgets})
    out = run_until_decision(world)
    assert out["agent_stats"]["stopped_by"] == "steps"
    assert out["agent_stats"]["tool_calls"] >= 1


def test_answer_request_lists_the_citable_ids() -> None:
    from auditguard.agents.single import answer_message
    from auditguard.hooks.tools import ToolRecord

    record = ToolRecord("evaluate_rules", {}, {"results": [{"rule_id": "R04"}]}, by_model=True)
    assert "Evidence IDs you may cite (no others): R04" in answer_message([record], [])
