"""submit (code): the only write. Re-hash the stored payload, check it equals the approved
hash, then call submit_approved_payload with the idempotency key (docs/08 §2).

On a timeout or an uncertain result it asks get_receipt before anything else; it never
retries blindly. The portal re-checks the approval and the hash on its side.
"""

from __future__ import annotations

from typing import Any

from auditguard.approval.canonical import sha256_hex
from auditguard.graph.deps import GraphDeps
from auditguard.graph.state import CaseState
from auditguard.hooks.tools import ToolSession, is_error, local_error
from auditguard.schemas import Receipt, ToolError
from mcp_servers.filing_portal.server import idempotency_key_for

CALLER = "submit"


def make_submit(deps: GraphDeps) -> Any:
    async def submit(state: CaseState) -> dict[str, Any]:
        payload, approval = state.get("payload"), state.get("approval")
        if payload is None or approval is None:
            err = local_error("APPROVAL_INVALID", "no approved payload to submit", CALLER)
            return {"status": "ERROR", "errors": [ToolError.model_validate(err)]}
        digest = sha256_hex(payload.canonical_json.encode("utf-8"))
        if digest != approval.payload_sha256 or digest != payload.sha256:
            err = local_error("STALE_HASH", "payload changed after approval", CALLER)
            return {"status": "ERROR", "errors": [ToolError.model_validate(err)]}
        key = idempotency_key_for(state["case_id"], digest)
        session = ToolSession(
            CALLER,
            state["case_id"],
            deps.settings,
            deps.clients,
            ("filing_portal",),
            clock=deps.clock,
        )
        async with session:
            result = await session.call(
                "submit_approved_payload",
                payload_sha256=digest,
                approval_id=approval.approval_id,
                idempotency_key=key,
            )
            if is_error(result) and result.get("error_code") == "TIMEOUT":
                result = await session.call("get_receipt", idempotency_key=key)
        if is_error(result):
            return {"status": "ERROR", "errors": [ToolError.model_validate(result)]}
        return {"receipt": Receipt.model_validate(result), "status": "SUBMITTED"}

    return submit
