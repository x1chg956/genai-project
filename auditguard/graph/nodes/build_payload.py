"""build_payload (code): map the latest draft to the STR payload, canonicalise, hash, and
store it as the portal's current draft through prepare_payload (docs/08 §1).

The local hash and the portal's must agree; the approval gate then shows exactly these bytes.
"""

from __future__ import annotations

from typing import Any

from auditguard.approval.canonical import canonical_json, sha256_hex
from auditguard.config import ConfigError
from auditguard.graph.deps import GraphDeps
from auditguard.graph.state import CaseState
from auditguard.hooks.tools import ToolSession, is_error, local_error
from auditguard.report.str_payload import build_str_payload
from auditguard.schemas import Payload, ToolError

CALLER = "build_payload"


def _error(message: str) -> dict[str, Any]:
    err = ToolError.model_validate(local_error("INVALID_INPUT", message, CALLER))
    return {"status": "ERROR", "errors": [err]}


def make_build_payload(deps: GraphDeps) -> Any:
    async def build_payload(state: CaseState) -> dict[str, Any]:
        result, decision, ledger = state.get("result"), state.get("decision"), state.get("ledger")
        drafts = state.get("drafts") or []
        if result is None or decision is None or ledger is None or not drafts:
            return _error("nothing to file: no draft, decision or ledger in the case")
        draft = drafts[-1]
        try:
            payload = build_str_payload(
                case_id=state["case_id"],
                report_version=draft.version,
                alert=state["alert"],
                ledger=ledger,
                indicators=result.indicators,
                draft=draft,
                decision=decision,
                report=deps.settings.str_report,
                reason_codes=deps.reason_codes,
                tz=deps.tz,
            )
        except ConfigError as exc:
            return _error(str(exc))
        data = canonical_json(payload)
        digest = sha256_hex(data)
        session = ToolSession(
            CALLER,
            state["case_id"],
            deps.settings,
            deps.clients,
            ("filing_portal",),
            clock=deps.clock,
        )
        async with session:
            preview = await session.call(
                "prepare_payload", case_model_version=draft.version, payload=payload
            )
        if is_error(preview):
            return {"status": "ERROR", "errors": [ToolError.model_validate(preview)]}
        if preview["sha256"] != digest:
            return _error("portal hash differs from the local canonical hash")
        if not preview["form_limit_ok"]:
            return _error("form limits: " + "; ".join(preview["form_limit_issues"]))
        return {
            "payload": Payload(
                report_version=draft.version, canonical_json=data.decode("utf-8"), sha256=digest
            ),
            "previous_payload": state.get("payload"),
            "approval": None,
            "status": "PENDING_APPROVAL",
        }

    return build_payload
