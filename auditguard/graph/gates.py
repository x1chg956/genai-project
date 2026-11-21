"""decision_gate and approval_gate: the two human interrupts (docs/08 §2).

Both nodes are safe to re-run: on resume LangGraph re-runs the interrupted node from its
start, and everything before `interrupt()` here only reads state.
"""

from __future__ import annotations

import difflib
import json
import secrets
from datetime import timedelta
from typing import Any

from langgraph.types import interrupt
from pydantic import ValidationError

from auditguard.approval.records import signed
from auditguard.config import require_secret
from auditguard.graph.deps import GraphDeps
from auditguard.graph.state import CaseState
from auditguard.schemas import ApprovalRecord, Decision, DraftReport, Payload

DECISIONS = ["file", "no_file", "needs_info"]
ACTIONS = ["approve", "edit", "reject"]


def evidence_view(state: CaseState) -> dict[str, Any]:
    result = state.get("result")
    quality = state.get("data_quality")
    return {
        "indicators": [r.model_dump(mode="json") for r in state.get("indicators", [])],
        "hypotheses": [h.model_dump(mode="json") for h in result.hypotheses] if result else [],
        "passages": [p.model_dump(mode="json") for p in result.passages] if result else [],
        "draft": result.draft.model_dump(mode="json") if result else None,
        "data_quality": quality.model_dump(mode="json") if quality else None,
        "errors": [e.model_dump(mode="json") for e in state.get("errors", [])],
        "agent_stats": state.get("agent_stats", {}),
    }


def payload_diff(previous: Payload | None, current: Payload) -> str:
    if previous is None:
        return ""

    def lines(p: Payload) -> list[str]:
        return json.dumps(json.loads(p.canonical_json), indent=2, sort_keys=True).splitlines()

    return "\n".join(
        difflib.unified_diff(
            lines(previous),
            lines(current),
            f"version {previous.report_version}",
            f"version {current.report_version}",
            lineterm="",
        )
    )


def make_decision_gate(deps: GraphDeps) -> Any:
    del deps

    def decision_gate(state: CaseState) -> dict[str, Any]:
        value = interrupt(
            {
                "kind": "decision",
                "case_id": state["case_id"],
                "evidence_pack": evidence_view(state),
                "options": DECISIONS,
                "notice": state.get("notice", ""),
            }
        )
        try:
            decision = Decision.model_validate(value)
        except ValidationError as exc:
            return {"decision": None, "notice": f"invalid decision: {exc.errors()[0]['msg']}"}
        if decision.decided_by != state["requester_id"]:
            return {"decision": None, "notice": "only the case's analyst can decide"}
        if decision.decision == "file" and state.get("result") is None:
            return {"decision": None, "notice": "no draft to file: choose no_file or needs_info"}
        return {"decision": decision, "notice": ""}

    return decision_gate


def route_decision(state: CaseState) -> str:
    decision = state.get("decision")
    if decision is None:
        return "decision_gate"
    return {"file": "build_payload", "no_file": "close_no_file", "needs_info": "needs_info"}[
        decision.decision
    ]


def make_approval_gate(deps: GraphDeps) -> Any:
    settings = deps.settings

    def approval_gate(state: CaseState) -> dict[str, Any]:
        payload = state.get("payload")
        assert payload is not None  # noqa: S101 - build_payload always sets it before this node
        value = interrupt(
            {
                "kind": "approval",
                "case_id": state["case_id"],
                "payload": payload.canonical_json,
                "sha256": payload.sha256,
                "report_version": payload.report_version,
                "diff": payload_diff(state.get("previous_payload"), payload),
                "options": ACTIONS,
                "requester_id": state["requester_id"],
                "notice": state.get("notice", ""),
            }
        )
        action = value.get("action") if isinstance(value, dict) else None
        if action == "reject":
            return {"status": "REJECTED", "notice": ""}
        if action == "edit":
            try:
                edited = DraftReport.model_validate(value.get("edited_draft"))
            except ValidationError as exc:
                return {"notice": f"edit refused: {exc.errors()[0]['msg']}"}
            drafts = state.get("drafts") or []
            new = edited.model_copy(update={"version": drafts[-1].version + 1})
            deps.approvals.void_case(state["case_id"])
            return {"drafts": [*drafts, new], "approval": None, "status": "DRAFTED", "notice": ""}
        if action != "approve":
            return {"notice": f"unknown action {action!r}; choose one of {', '.join(ACTIONS)}"}
        approver = str(value.get("approver_id") or "").strip()
        if not approver:
            return {"notice": "an approval needs the approver's ID"}
        if approver == state["requester_id"]:
            return {"notice": "self-approval refused: the approver must not be the requester"}
        key = require_secret(settings.approval.hmac_key, "APPROVAL_HMAC_KEY")
        record = signed(
            ApprovalRecord(
                approval_id=f"APR-SYN-{secrets.token_hex(6)}",
                case_id=state["case_id"],
                payload_sha256=payload.sha256,
                report_version=payload.report_version,
                requester_id=state["requester_id"],
                approver_id=approver,
                role="principal_officer",
                decision="approve",
                expires_at=deps.clock() + timedelta(minutes=settings.approval.ttl_minutes),
                hmac="",
            ),
            key,
        )
        deps.approvals.put(record)
        return {"approval": record, "status": "APPROVED", "notice": ""}

    return approval_gate


def route_approval(state: CaseState) -> str:
    status = state.get("status")
    if status == "REJECTED":
        return "end"
    if status == "DRAFTED":
        return "build_payload"
    if status == "APPROVED" and state.get("approval") is not None:
        return "submit"
    return "approval_gate"
