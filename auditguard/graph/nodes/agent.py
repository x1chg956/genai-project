"""single_agent node: runs the C1 ReAct agent with the single_agent caller's tools."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from langchain_core.runnables import RunnableConfig

from auditguard.agents.single import CALLER, run_single_agent
from auditguard.graph.deps import GraphDeps
from auditguard.graph.state import CaseState
from auditguard.hooks.tools import ToolSession

SERVERS = ("transactions", "aml_engine", "regulatory_kb")


def make_agent_node(deps: GraphDeps) -> Any:
    async def single_agent(state: CaseState, config: RunnableConfig) -> dict[str, Any]:
        ledger, quality = state.get("ledger"), state.get("data_quality")
        if ledger is None or quality is None:
            return {"status": "ERROR"}
        budget = deps.settings.budgets.case
        session = ToolSession(
            CALLER,
            state["case_id"],
            deps.settings,
            deps.clients,
            SERVERS,
            max_calls=budget.tool_calls,
            clock=deps.clock,
        )
        async with session:
            outcome = await run_single_agent(
                alert=state["alert"],
                ledger=ledger,
                quality=quality,
                session=session,
                chat_model=deps.chat_model,
                answer_model=deps.answer_model,
                prompt=deps.prompt,
                budget=budget,
                config=RunnableConfig(callbacks=config.get("callbacks")),
            )
        update: dict[str, Any] = {
            "result": outcome.result,
            "indicators": outcome.indicators,
            "agent_stats": asdict(outcome.stats),
        }
        if outcome.result is not None:
            update["drafts"] = [outcome.result.draft]
            update["status"] = "DRAFTED"
        if outcome.error is not None:
            update["errors"] = [outcome.error]
        return update

    return single_agent
