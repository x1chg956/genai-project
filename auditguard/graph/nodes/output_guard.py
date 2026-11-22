"""output_guard (code, Phase 2): rule-based output guardrails on the latest draft, between
build_payload and approval_gate (docs/03 §2, docs/08 §4).

A critical finding ends the run as RETURNED, so the analyst gets the case back and no
Principal Officer is asked to approve it. Warnings travel to the approval gate.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from auditguard.checks.evidence import known_ids
from auditguard.graph.deps import GraphDeps
from auditguard.graph.state import CaseState
from auditguard.guardrails.output import OutputPolicy, blocking, check_draft
from auditguard.hooks.tools import ToolRecord


def records_in(state: CaseState) -> list[ToolRecord]:
    return [
        ToolRecord(r["tool"], r["args"], r["result"], by_model=True, ref=r.get("ref", ""))
        for r in state.get("tool_records", [])
    ]


def make_output_guard(deps: GraphDeps, policy: OutputPolicy) -> Any:
    def output_guard(state: CaseState) -> dict[str, Any]:
        drafts = state.get("drafts") or []
        if not drafts:
            return {"status": "ERROR"}
        known = known_ids(records_in(state))
        findings = check_draft(drafts[-1], known, deps.settings.guardrails.output, policy)
        update: dict[str, Any] = {"guard_findings": [asdict(f) for f in findings]}
        if blocking(findings):
            update["status"] = "RETURNED"
        return update

    return output_guard


def route_output_guard(state: CaseState) -> str:
    return "end" if state.get("status") in ("RETURNED", "ERROR") else "approval_gate"
