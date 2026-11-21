"""filing_portal server: prepare_payload (W, draft row only), submit_approved_payload (W),
get_receipt (R).

submit_approved_payload is the system's only write to the outside world (rule 3). In one
transaction it loads the approval record, checks it (decision, not void, Principal Officer,
approver != requester, not expired, HMAC), re-hashes the stored draft and compares it with
the approved hash, checks the idempotency key, then inserts the submission and receipt.
Only the `submit` code node holds a token for it.
"""

from __future__ import annotations

import secrets
from typing import Any, Literal

from fastmcp import FastMCP

from auditguard.approval.canonical import CanonicalError, canonical_json, sha256_hex
from auditguard.approval.records import ApprovalStore, problems
from auditguard.config import FormLimits, Settings, require_secret, resolve_path
from auditguard.schemas import Receipt, ToolError
from mcp_servers.common.errors import tool_error
from mcp_servers.common.guard import Clock, ToolGuard, guard_from_settings, utcnow
from mcp_servers.common.labels import READ_ONLY, WRITES
from mcp_servers.filing_portal.db import PortalDb
from mcp_servers.filing_portal.models import PayloadPreview

_SHA256_CHARS = set("0123456789abcdef")


def idempotency_key_for(case_id: str, payload_sha256: str) -> str:
    return f"{case_id}:{payload_sha256}"


def form_issues(payload: dict[str, Any], data: bytes, limits: FormLimits) -> list[str]:
    issues = []
    if len(data) > limits.max_payload_bytes:
        issues.append(f"payload is {len(data)} bytes; limit {limits.max_payload_bytes}")
    grounds = payload.get("grounds_of_suspicion")
    if isinstance(grounds, str) and len(grounds) > limits.max_grounds_chars:
        issues.append(
            f"grounds_of_suspicion is {len(grounds)} characters; limit {limits.max_grounds_chars}"
        )
    return issues


def build_server(
    db: PortalDb,
    approvals: ApprovalStore,
    guard: ToolGuard,
    *,
    approval_key: bytes,
    limits: FormLimits,
    clock: Clock = utcnow,
) -> FastMCP:
    mcp = FastMCP("filing_portal", middleware=[guard])

    @mcp.tool(tags={"write"}, annotations=WRITES)
    def prepare_payload(
        agent_token: str,
        case_id: str,
        case_model_version: int,
        payload: dict[str, Any],
        profile: Literal["IN"] = "IN",
    ) -> PayloadPreview | ToolError:
        """W (draft row only). Canonicalise the STR payload, hash it (SHA-256), check the
        form limits and store it as the case's current draft. Floats are refused: money is
        a decimal string. Submitting nothing; approval is bound to the returned sha256."""
        tool = "prepare_payload"
        if case_model_version < 1:
            return tool_error("INVALID_INPUT", "case_model_version must be >= 1", tool)
        try:
            data = canonical_json(payload)
        except CanonicalError as exc:
            return tool_error("INVALID_INPUT", str(exc), tool)
        digest = sha256_hex(data)
        text = data.decode("utf-8")
        with db.transaction() as conn:
            db.add_draft(
                conn,
                case_id=case_id,
                report_version=case_model_version,
                profile=profile,
                canonical_json=text,
                sha256=digest,
                now=clock(),
            )
        issues = form_issues(payload, data, limits)
        return PayloadPreview(
            case_id=case_id,
            report_version=case_model_version,
            canonical_json=text,
            sha256=digest,
            form_limit_ok=not issues,
            form_limit_issues=issues,
        )

    @mcp.tool(tags={"write"}, annotations=WRITES)
    def submit_approved_payload(
        agent_token: str,
        case_id: str,
        payload_sha256: str,
        approval_id: str,
        idempotency_key: str,
    ) -> Receipt | ToolError:
        """W. File the case's current draft. Requires an unexpired, unvoided Principal
        Officer approval of exactly this sha256 by someone other than the requester.
        idempotency_key must be "<case_id>:<payload_sha256>"; a repeat returns the same
        receipt. STALE_HASH means the draft changed after approval."""
        tool = "submit_approved_payload"
        if len(payload_sha256) != 64 or not set(payload_sha256) <= _SHA256_CHARS:
            return tool_error("INVALID_INPUT", "payload_sha256 must be 64 lowercase hex", tool)
        if idempotency_key != idempotency_key_for(case_id, payload_sha256):
            return tool_error(
                "INVALID_INPUT", "idempotency_key must be '<case_id>:<payload_sha256>'", tool
            )
        now = clock()
        with db.transaction() as conn:
            record = approvals.get(approval_id)
            if record is None:
                return tool_error("APPROVAL_INVALID", "no such approval", tool)
            found = problems(record, case_id=case_id, key=approval_key, now=now)
            if found:
                return tool_error("APPROVAL_INVALID", "; ".join(found), tool)
            if record.payload_sha256 != payload_sha256:
                return tool_error(
                    "STALE_HASH", "the approval is for a different payload hash", tool
                )
            draft = db.latest_draft(conn, case_id)
            if draft is None:
                return tool_error("NOT_FOUND", "no prepared payload for this case", tool)
            rehash = sha256_hex(draft.canonical_json.encode("utf-8"))
            if rehash != draft.sha256 or rehash != payload_sha256:
                return tool_error(
                    "STALE_HASH",
                    "the current payload no longer matches the approved hash; re-approve",
                    tool,
                )
            existing = db.receipt(conn, case_id, idempotency_key)
            if existing is not None:
                return existing
            receipt = Receipt(
                ack_id=f"ACK-SYN-{secrets.token_hex(6).upper()}",
                case_id=case_id,
                payload_sha256=payload_sha256,
                ts=now,
            )
            db.add_submission(
                conn,
                idempotency_key=idempotency_key,
                draft=draft,
                approval_id=approval_id,
                receipt=receipt,
            )
            return receipt

    @mcp.tool(tags={"read"}, annotations=READ_ONLY)
    def get_receipt(agent_token: str, case_id: str, idempotency_key: str) -> Receipt | ToolError:
        """R. The receipt for a submission, if one was made. Call this before any retry of
        submit_approved_payload after a timeout."""
        with db.transaction() as conn:
            receipt = db.receipt(conn, case_id, idempotency_key)
        if receipt is None:
            return tool_error("NOT_FOUND", "no receipt for this idempotency key", "get_receipt")
        return receipt

    return mcp


def build_from_settings(settings: Settings) -> FastMCP:
    return build_server(
        PortalDb(resolve_path(settings.filing_portal.db_path)),
        ApprovalStore(resolve_path(settings.approval.store_path), read_only=True),
        guard_from_settings("filing_portal", settings),
        approval_key=require_secret(settings.approval.hmac_key, "APPROVAL_HMAC_KEY"),
        limits=settings.filing_portal.form_limits,
    )
