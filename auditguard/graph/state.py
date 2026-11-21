"""CaseState for the C1 graph (docs/03 §4, Phase 1 subset).

Evidence, drafts, the payload and the approval live in typed fields, never only in messages.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from auditguard.schemas import (
    Alert,
    ApprovalRecord,
    DataQualityReport,
    Decision,
    DraftReport,
    LedgerSlice,
    Payload,
    Receipt,
    RuleResult,
    SingleAgentResult,
    ToolError,
)

Status = Literal[
    "NEW",
    "IN_PROGRESS",
    "NEEDS_INFO",
    "DRAFTED",
    "PENDING_APPROVAL",
    "APPROVED",
    "SUBMITTED",
    "CLOSED_NO_FILE",
    "REJECTED",
    "ERROR",
]


class CaseState(TypedDict, total=False):
    case_id: str
    run_id: str
    alert: Alert
    requester_id: str
    """The analyst who runs the case and decides at decision_gate; never the approver."""
    status: Status
    ledger: LedgerSlice | None
    data_quality: DataQualityReport | None
    result: SingleAgentResult | None
    indicators: list[RuleResult]
    """Rule results from the tool; shown at the decision gate even if the answer failed."""
    agent_stats: dict[str, Any]
    tool_records: list[dict[str, Any]]
    """The agent's tool calls and results (tool, args, result); its claims are checked on these."""
    decision: Decision | None
    drafts: list[DraftReport]
    payload: Payload | None
    previous_payload: Payload | None
    approval: ApprovalRecord | None
    receipt: Receipt | None
    notice: str
    """Why a gate asked again (e.g. self-approval refused); shown at the next interrupt."""
    errors: Annotated[list[ToolError], operator.add]
