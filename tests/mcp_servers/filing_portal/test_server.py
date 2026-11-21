"""filing_portal server: the only write. Approval checks and hash binding (rule 3)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from auditguard.approval.canonical import payload_hash
from auditguard.approval.records import ApprovalStore, signed
from auditguard.config import FormLimits
from auditguard.schemas import ApprovalRecord
from mcp_servers.filing_portal.db import PortalDb
from mcp_servers.filing_portal.server import build_server, idempotency_key_for
from tests.conftest import TEST_ENV
from tests.mcp_servers.helpers import CASE, NOW, OTHER_CASE, Called, call, guard, token

APPROVAL_KEY = TEST_ENV["APPROVAL_HMAC_KEY"].encode()
LIMITS = FormLimits(max_payload_bytes=2000, max_grounds_chars=100)
PAYLOAD: dict[str, Any] = {
    "report": {"type": "STR", "case_id": CASE},
    "accounts": [{"account_token": "ACCT-SYN-000001", "amount_total": "1250000.00"}],
    "grounds_of_suspicion": "Credits passed out within 48 hours (R04).",
}


class Portal:
    def __init__(self, tmp: Path, *, now_offset: timedelta = timedelta(0)) -> None:
        self.approvals = ApprovalStore(tmp / "approvals.db")
        self.db = PortalDb(tmp / "portal.db")
        self.server: FastMCP = build_server(
            self.db,
            ApprovalStore(tmp / "approvals.db", read_only=True),
            guard("filing_portal"),
            approval_key=APPROVAL_KEY,
            limits=LIMITS,
            clock=lambda: NOW + now_offset,
        )

    def run(self, tool: str, agent: str, **args: Any) -> Called:
        return call(self.server, tool, {"agent_token": token(agent), "case_id": CASE} | args)

    def prepare(self, payload: dict[str, Any] = PAYLOAD, version: int = 1) -> Called:
        return self.run(
            "prepare_payload", "build_payload", case_model_version=version, payload=payload
        )

    def approve(self, sha: str, approval_id: str = "APR-1", **over: Any) -> str:
        key = over.pop("key", APPROVAL_KEY)
        record = ApprovalRecord.model_validate(
            {
                "approval_id": approval_id,
                "case_id": CASE,
                "payload_sha256": sha,
                "report_version": 1,
                "requester_id": "analyst-1",
                "approver_id": "po-1",
                "role": "principal_officer",
                "decision": "approve",
                "expires_at": NOW + timedelta(minutes=120),
                "hmac": "",
            }
            | over
        )
        self.approvals.put(signed(record, key))
        return approval_id

    def submit(self, sha: str, approval_id: str = "APR-1", key: str | None = None) -> Called:
        return self.run(
            "submit_approved_payload",
            "submit",
            payload_sha256=sha,
            approval_id=approval_id,
            idempotency_key=key or idempotency_key_for(CASE, sha),
        )


@pytest.fixture
def portal(tmp_path: Path) -> Portal:
    return Portal(tmp_path)


# prepare_payload


def test_prepare_payload_happy(portal: Portal) -> None:
    r = portal.prepare()
    assert not r.is_error
    assert r.result["sha256"] == payload_hash(PAYLOAD)
    assert r.result["canonical_json"].startswith('{"accounts":[{"account_token"')
    assert r.result["form_limit_ok"] is True


def test_prepare_payload_form_limits(portal: Portal) -> None:
    r = portal.prepare({**PAYLOAD, "grounds_of_suspicion": "x" * 101})
    assert r.result["form_limit_ok"] is False
    assert "grounds_of_suspicion" in r.result["form_limit_issues"][0]


@pytest.mark.parametrize(
    "payload",
    [
        {"amount": 1250000.5},  # a float: money must be a decimal string
        {"nested": [{"x": 0.1}]},
    ],
)
def test_prepare_payload_invalid_input(portal: Portal, payload: dict[str, Any]) -> None:
    r = portal.prepare(payload)
    assert r.is_error and r.code == "INVALID_INPUT"
    assert portal.prepare(PAYLOAD, version=0).code == "INVALID_INPUT"
    assert portal.run("prepare_payload", "build_payload", payload=PAYLOAD).code == "INVALID_INPUT"


# submit_approved_payload


def test_submit_approved_payload_happy(portal: Portal) -> None:
    sha = portal.prepare().result["sha256"]
    portal.approve(sha)
    r = portal.submit(sha)
    assert not r.is_error, r.result
    assert r.result["ack_id"].startswith("ACK-SYN-")
    assert (r.result["case_id"], r.result["payload_sha256"]) == (CASE, sha)


def test_submit_stale_hash(portal: Portal) -> None:
    """Editing after approval: the new draft no longer matches the approved hash."""
    sha1 = portal.prepare().result["sha256"]
    portal.approve(sha1)
    edited = {**PAYLOAD, "grounds_of_suspicion": "Edited after approval."}
    sha2 = portal.prepare(edited, version=2).result["sha256"]
    assert sha2 != sha1
    r = portal.submit(sha1)
    assert r.is_error and r.code == "STALE_HASH"
    # The old approval cannot be reused for the new hash either.
    r = portal.submit(sha2)
    assert r.code == "STALE_HASH"


def test_submit_self_approval(portal: Portal) -> None:
    sha = portal.prepare().result["sha256"]
    portal.approve(sha, approver_id="analyst-1")
    r = portal.submit(sha)
    assert r.code == "APPROVAL_INVALID" and "self-approval" in r.result["message"]


def test_submit_expired(tmp_path: Path) -> None:
    early = Portal(tmp_path)
    sha = early.prepare().result["sha256"]
    early.approve(sha)
    late = Portal(tmp_path, now_offset=timedelta(minutes=120))
    r = late.submit(sha)
    assert r.code == "APPROVAL_INVALID" and "expired" in r.result["message"]


def test_submit_idempotent(portal: Portal) -> None:
    sha = portal.prepare().result["sha256"]
    portal.approve(sha)
    first, second = portal.submit(sha), portal.submit(sha)
    assert first.result == second.result
    with portal.db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM receipts").fetchone() == (1,)


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"decision": "reject"}, "not 'approve'"),
        ({"void": True}, "voided"),
        ({"case_id": OTHER_CASE}, "another case"),
    ],
)
def test_submit_approval_invalid(portal: Portal, over: dict[str, Any], message: str) -> None:
    sha = portal.prepare().result["sha256"]
    portal.approve(sha, **over)
    r = portal.submit(sha)
    assert r.code == "APPROVAL_INVALID" and message in r.result["message"]


def test_submit_forged_signature_and_missing_approval(portal: Portal) -> None:
    sha = portal.prepare().result["sha256"]
    portal.approve(sha, key=b"attacker-key")
    assert portal.submit(sha).code == "APPROVAL_INVALID"
    assert portal.submit(sha, approval_id="APR-404").code == "APPROVAL_INVALID"


def test_submit_approved_payload_invalid_input(portal: Portal) -> None:
    sha = portal.prepare().result["sha256"]
    portal.approve(sha)
    assert portal.submit(sha, key=f"{CASE}:wrong").code == "INVALID_INPUT"
    assert portal.submit("ABC").code == "INVALID_INPUT"


# get_receipt


def test_get_receipt_happy(portal: Portal) -> None:
    sha = portal.prepare().result["sha256"]
    portal.approve(sha)
    receipt = portal.submit(sha).result
    r = portal.run("get_receipt", "submit", idempotency_key=idempotency_key_for(CASE, sha))
    assert r.result == receipt


def test_get_receipt_invalid_input(portal: Portal) -> None:
    assert portal.run("get_receipt", "submit", idempotency_key="nope").code == "NOT_FOUND"
    assert portal.run("get_receipt", "submit").code == "INVALID_INPUT"


# identity and scope


def test_wrong_agent_token(portal: Portal) -> None:
    """Rule 3: only the submit code node can write; no agent holds that token."""
    sha = portal.prepare().result["sha256"]
    portal.approve(sha)
    for agent in ("single_agent", "build_payload", "investigator", "researcher", "critic"):
        r = portal.run(
            "submit_approved_payload",
            agent,
            payload_sha256=sha,
            approval_id="APR-1",
            idempotency_key=idempotency_key_for(CASE, sha),
        )
        assert r.code == "FORBIDDEN", agent
    assert (
        portal.run("prepare_payload", "single_agent", case_model_version=1, payload=PAYLOAD).code
        == "FORBIDDEN"
    )


def test_out_of_scope_case(portal: Portal) -> None:
    r = call(
        portal.server,
        "get_receipt",
        {"agent_token": token("submit", OTHER_CASE), "case_id": CASE, "idempotency_key": "x"},
    )
    assert r.code == "OUT_OF_SCOPE"


def test_decimal_payload_value_round_trips(portal: Portal) -> None:
    r = portal.prepare({"amount": str(Decimal("10.50"))})
    assert r.result["canonical_json"] == '{"amount":"10.50"}'
