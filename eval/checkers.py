"""Code checkers (docs/10 §4) over one finished run. Pure functions, no model.

Each returns PASS, FAIL or SKIP with a reason. SKIP means the case does not state the
expectation yet (a scaffold), never that the checker could not decide. Invariant checks
(no write tool used by the agent, budget kept, numbers and citations traced to tools) run
on every case whether or not its expectations are filled in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from auditguard.checks.evidence import known_ids, tool_numbers, unresolved_ids, unsupported_numbers
from auditguard.hooks.tools import ToolRecord
from auditguard.schemas import SingleAgentResult
from eval.cases import EvalCase

Status = Literal["PASS", "FAIL", "SKIP"]

TOOL_CLASSES = {
    "rules": {"evaluate_rules", "get_rule_metadata"},
    "drilldown": {"get_transactions", "get_account_summary", "get_counterparty_flows"},
    "retrieval": {"search_passages", "get_passage"},
}
WRITE_TOOLS = {"prepare_payload", "submit_approved_payload"}


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str = ""


def records_of(state: dict[str, Any]) -> list[ToolRecord]:
    return [
        ToolRecord(r["tool"], r["args"], r["result"], by_model=True)
        for r in state.get("tool_records", [])
    ]


def check_disposition(case: EvalCase, state: dict[str, Any]) -> Check:
    if case.expected_disposition is None:
        return Check("disposition", "SKIP", "expected_disposition not set (role 1)")
    result: SingleAgentResult | None = state.get("result")
    if result is None:
        return Check("disposition", "FAIL", "no accepted agent answer, so no recommendation")
    ok = result.recommendation == case.expected_disposition
    return Check(
        "disposition",
        "PASS" if ok else "FAIL",
        f"recommended {result.recommendation}, expected {case.expected_disposition}",
    )


def check_rules(case: EvalCase, state: dict[str, Any]) -> Check:
    if case.expected_rule_outputs is None:
        return Check("rules", "SKIP", "expected_rule_outputs not set (role 1)")
    fired = {r.rule_id for r in state.get("indicators", []) if r.fired}
    missing = sorted(set(case.expected_rule_outputs.must_fire) - fired)
    extra = sorted(set(case.expected_rule_outputs.must_not_fire) & fired)
    if missing or extra:
        return Check("rules", "FAIL", f"missing {missing}, unexpected {extra}")
    return Check("rules", "PASS", f"fired {sorted(fired)}")


def check_numbers(state: dict[str, Any]) -> Check:
    result: SingleAgentResult | None = state.get("result")
    if result is None:
        return Check("numbers", "SKIP", "no draft")
    # The agent's own draft: a person editing at the approval gate may write any number.
    bad = unsupported_numbers(result.draft, tool_numbers(records_of(state)))
    return Check("numbers", "FAIL" if bad else "PASS", f"not from tools: {bad}" if bad else "")


def check_citations(state: dict[str, Any]) -> Check:
    result: SingleAgentResult | None = state.get("result")
    if result is None:
        return Check("citations", "SKIP", "no draft")
    missing = unresolved_ids(result.draft, known_ids(records_of(state)))
    cited = len(result.draft.cited_ids())
    if missing:
        return Check("citations", "FAIL", f"{len(missing)} of {cited} unresolved: {missing}")
    return Check("citations", "PASS", f"{cited} cited, all resolve")


def check_trajectory(case: EvalCase, state: dict[str, Any], budget_calls: int) -> Check:
    called = [r["tool"] for r in state.get("tool_records", [])]
    used_writes = sorted(WRITE_TOOLS & set(called))
    if used_writes:
        return Check("trajectory", "FAIL", f"agent called write tools {used_writes}")
    limit = budget_calls
    if case.expected_trajectory and case.expected_trajectory.max_tool_calls is not None:
        limit = min(limit, case.expected_trajectory.max_tool_calls)
    if len(called) > limit:
        return Check("trajectory", "FAIL", f"{len(called)} tool calls, limit {limit}")
    if case.expected_trajectory is None:
        return Check("trajectory", "PASS", f"{len(called)} calls, no write tool (classes unset)")
    forbidden = sorted(set(case.expected_trajectory.forbidden_tools) & set(called))
    absent = [
        c
        for c in case.expected_trajectory.required_tool_classes
        if not TOOL_CLASSES[c] & set(called)
    ]
    if forbidden or absent:
        return Check("trajectory", "FAIL", f"forbidden {forbidden}, missing classes {absent}")
    return Check("trajectory", "PASS", f"{len(called)} calls")


def check_final_state(case: EvalCase, state: dict[str, Any]) -> Check:
    if case.gate_script is None:
        return Check("final_state", "SKIP", "gate_script not set (role 1); answered needs_info")
    should_file = case.gate_script.decision == "file" and case.gate_script.approval == "approve"
    has_receipt = state.get("receipt") is not None
    ok = has_receipt == should_file
    return Check(
        "final_state",
        "PASS" if ok else "FAIL",
        f"status {state.get('status')}, receipt {'yes' if has_receipt else 'no'}",
    )


def run_checks(case: EvalCase, state: dict[str, Any], budget_calls: int) -> list[Check]:
    return [
        check_disposition(case, state),
        check_rules(case, state),
        check_numbers(state),
        check_citations(state),
        check_trajectory(case, state, budget_calls),
        check_final_state(case, state),
    ]
