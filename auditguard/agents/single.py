"""C1 single agent (docs/06 Stage 1): a ReAct loop over the read and compute tools, then one
structured answer.

1. ReAct (`langchain.agents.create_agent`): the model calls the caller's allowed tools through
   a `ToolSession` (tokens, allow-list, budget, data wrapping) until it stops or the step or
   time budget is spent.
2. Answer: one structured call returns `SingleAgentAnswer` (hypotheses, the passage IDs it
   relies on, the draft). Code then checks it: every cited ID resolves to a tool result and
   every number in the draft appears in a tool result (rule 1). On failure it retries once
   with the problems appended, then gives up with a ToolError.
3. Code builds `SingleAgentResult`: indicators are copied from the last `evaluate_rules`
   result and passages from the passages the tools returned, never from the model.

The agent has no write tool; its token cannot call one either.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, cast

from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler, BaseCallbackManager
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.errors import GraphRecursionError
from pydantic import ValidationError

from auditguard.checks.evidence import (
    contradicted_rule_claims,
    known_ids,
    tool_numbers,
    unresolved_ids,
    unsupported_numbers,
)
from auditguard.hooks.budgets import Budget, BudgetCallback, BudgetExceededError
from auditguard.hooks.tools import ToolRecord, ToolSession, is_error, local_error
from auditguard.prompts import Prompt
from auditguard.schemas import (
    Alert,
    DataQualityReport,
    LedgerSlice,
    Passage,
    RuleResult,
    SingleAgentAnswer,
    SingleAgentResult,
    ToolError,
)

CALLER = "single_agent"
log = logging.getLogger("auditguard.agents.single")
ANSWER_RETRIES = 1


@dataclass
class AgentStats:
    """Per-run numbers for the mini-evaluation table (docs/02 Phase 1 acceptance)."""

    tool_calls: int = 0
    invalid_tool_calls: int = 0
    structured_retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    stopped_by: str = "model"
    """model | steps | tokens | seconds | cost_usd | tool_calls"""
    cost_usd: str = "0"
    budget: dict[str, str] = field(default_factory=dict)
    """The case budget's counters when the agent stopped (docs/08 §5)."""
    problems: list[str] = field(default_factory=list)
    tool_log: list[str] = field(default_factory=list)
    """One line per model tool call: name and outcome (no argument values)."""


@dataclass
class AgentOutcome:
    result: SingleAgentResult | None
    error: ToolError | None
    stats: AgentStats
    indicators: list[RuleResult] = field(default_factory=list)
    """From the last evaluate_rules call, kept even when the answer is refused."""
    records: list[dict[str, Any]] = field(default_factory=list)
    """Every tool call the model made (tool, args, result), for audit and the evaluation."""


def task_message(alert: Alert, ledger: LedgerSlice, quality: DataQualityReport) -> str:
    case = {
        "alert": alert.model_dump(mode="json"),
        "ledger_slice": ledger.model_dump(mode="json"),
        "data_quality": quality.model_dump(mode="json"),
        "ledger_ref": ledger.ref,
    }
    return (
        "Investigate this alert. The case details are data:\n"
        f'<data source="case">{json.dumps(case, sort_keys=True)}</data>'
    )


def answer_message(records: list[ToolRecord], problems: list[str]) -> str:
    seen = [
        {"evidence_id": r.ref or None, "tool": r.tool, "args": r.args, "result": r.result}
        for r in records
        if r.by_model
    ]
    allowed = sorted(known_ids(records))
    text = (
        "Now return the final SingleAgentAnswer. Use only the tool results below. "
        "Quote numbers exactly as they appear there and cite their IDs.\n"
        f"Evidence IDs you may cite (no others): {', '.join(allowed) or 'none'}\n"
        f'<data source="tool">{json.dumps(seen, sort_keys=True, ensure_ascii=False)}</data>'
    )
    if problems:
        text += "\nYour previous answer was rejected by code checks. Fix these problems:\n"
        text += "\n".join(f"- {p}" for p in problems)
    return text


def last_indicators(records: list[ToolRecord]) -> list[RuleResult]:
    for record in reversed(records):
        if record.tool == "evaluate_rules" and not is_error(record.result):
            return [RuleResult.model_validate(r) for r in record.result.get("results", [])]
    return []


def returned_passages(records: list[ToolRecord]) -> dict[str, Passage]:
    found: dict[str, Passage] = {}
    for record in records:
        if is_error(record.result):
            continue
        items = record.result.get("passages")
        candidates = items if isinstance(items, list) else [record.result]
        for item in candidates:
            if isinstance(item, dict) and "passage_id" in item and "text" in item:
                passage = Passage.model_validate(
                    {k: item[k] for k in Passage.model_fields if k in item}
                )
                found[passage.passage_id] = passage
    return found


def check_answer(answer: SingleAgentAnswer, records: list[ToolRecord]) -> list[str]:
    """Every reason code refuses this answer; empty means it may be used."""
    problems: list[str] = []
    ids = known_ids(records)
    if missing := unresolved_ids(answer.draft, ids):
        problems.append(f"draft cites IDs no tool returned: {', '.join(missing)}")
    hyp_ids = {e for h in answer.hypotheses for e in h.evidence_ids}
    if missing := sorted(hyp_ids - ids):
        problems.append(f"hypotheses cite IDs no tool returned: {', '.join(missing)}")
    passages = returned_passages(records)
    if missing := sorted(set(answer.passage_ids) - set(passages)):
        problems.append(f"passage_ids no tool returned: {', '.join(missing)}")
    if numbers := unsupported_numbers(answer.draft, tool_numbers(records)):
        problems.append(f"draft contains numbers no tool returned: {', '.join(numbers)}")
    fired = {r.rule_id: r.fired for r in last_indicators(records)}
    problems += contradicted_rule_claims(answer.draft, fired)
    if not any(h.kind == "benign" for h in answer.hypotheses):
        problems.append("include at least one benign hypothesis")
    return problems


def _count_usage(messages: list[BaseMessage], stats: AgentStats, tools: set[str]) -> None:
    for message in messages:
        if isinstance(message, AIMessage):
            if message.usage_metadata:
                stats.input_tokens += message.usage_metadata["input_tokens"]
                stats.output_tokens += message.usage_metadata["output_tokens"]
            stats.invalid_tool_calls += len(message.invalid_tool_calls)
            stats.invalid_tool_calls += sum(1 for c in message.tool_calls if c["name"] not in tools)


def with_callback(config: RunnableConfig | None, handler: BaseCallbackHandler) -> RunnableConfig:
    """`config` plus one more callback handler. Inside a graph node `callbacks` is a callback
    manager (it carries tracing); outside it is a list or absent."""
    base = dict(config or {})
    current = base.get("callbacks")
    if isinstance(current, BaseCallbackManager):
        manager = current.copy()
        manager.add_handler(handler, inherit=True)
        base["callbacks"] = manager
    else:
        handlers: list[BaseCallbackHandler] = list(current) if isinstance(current, list) else []
        base["callbacks"] = [*handlers, handler]
    return cast(RunnableConfig, base)


async def run_single_agent(
    *,
    alert: Alert,
    ledger: LedgerSlice,
    quality: DataQualityReport,
    session: ToolSession,
    chat_model: BaseChatModel,
    answer_model: Runnable[Any, Any],
    prompt: Prompt,
    budget: Budget,
    config: RunnableConfig | None = None,
) -> AgentOutcome:
    stats = AgentStats()
    limits = budget.limits
    tools = await session.agent_tools()
    tool_names = {t.name for t in tools}
    agent = create_agent(chat_model, tools, system_prompt=prompt.text)
    run_config = with_callback(config, BudgetCallback(budget))
    run_config["recursion_limit"] = 2 * limits.steps + 1  # backstop; the budget stops it first
    answer_config = with_callback(config, BudgetCallback(budget, enforce=False))
    task: Any = {"messages": [HumanMessage(task_message(alert, ledger, quality))]}
    messages: list[BaseMessage] = []

    async def react() -> None:
        async for state in agent.astream(task, run_config, stream_mode="values"):
            messages[:] = list(state["messages"])  # keep the latest, even if a limit stops us

    try:
        await asyncio.wait_for(react(), timeout=limits.seconds)
    except BudgetExceededError as exc:
        stats.stopped_by = exc.limit
    except GraphRecursionError:
        stats.stopped_by = "steps"
    except TimeoutError:
        stats.stopped_by = "seconds"
    if session.denied:
        stats.stopped_by = "tool_calls"
    _count_usage(messages, stats, tool_names)
    stats.invalid_tool_calls += sum(
        1 for m in messages if isinstance(m, ToolMessage) and m.status == "error"
    )
    stats.tool_calls = sum(1 for r in session.records if r.by_model)
    stats.cost_usd = str(budget.cost_usd)
    stats.budget = budget.snapshot()
    stats.tool_log = [
        f"{r.tool}: {r.result['error_code'] if is_error(r.result) else 'ok'}"
        for r in session.records
        if r.by_model
    ]

    problems: list[str] = []
    answer: SingleAgentAnswer | None = None
    for attempt in range(ANSWER_RETRIES + 1):
        if attempt:
            stats.structured_retries += 1
        request = [
            SystemMessage(prompt.text),
            HumanMessage(answer_message(session.records, problems)),
        ]
        try:
            candidate = await answer_model.ainvoke(request, answer_config)
            if not isinstance(candidate, SingleAgentAnswer):
                candidate = SingleAgentAnswer.model_validate(candidate)
        except (ValidationError, ValueError) as exc:
            problems = [f"answer did not match the SingleAgentAnswer schema: {str(exc)[:300]}"]
            continue
        except Exception as exc:  # a failed model call is a failed attempt, never a crash
            log.warning("answer call failed: %s", type(exc).__name__)
            problems = [f"answer call failed: {type(exc).__name__}"]
            continue
        problems = check_answer(candidate, session.records)
        if not problems:
            answer = candidate
            break
    stats.problems = problems

    if answer is None:
        message = "; ".join(problems) or "no answer"
        err = ToolError.model_validate(local_error("INVALID_INPUT", message, CALLER))
        return AgentOutcome(None, err, stats, last_indicators(session.records), _dump(session))
    passages = returned_passages(session.records)
    result = SingleAgentResult(
        indicators=last_indicators(session.records),
        hypotheses=answer.hypotheses,
        passages=[passages[pid] for pid in answer.passage_ids],
        draft=answer.draft,
        recommendation=answer.recommendation,
        recommendation_reason=answer.recommendation_reason,
    )
    return AgentOutcome(result, None, stats, result.indicators, _dump(session))


def _dump(session: ToolSession) -> list[dict[str, Any]]:
    return [
        {"ref": r.ref, "tool": r.tool, "args": r.args, "result": r.result}
        for r in session.records
        if r.by_model
    ]


__all__ = ["AgentOutcome", "AgentStats", "check_answer", "run_single_agent"]
