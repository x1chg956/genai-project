"""Payload, approval record and receipt (docs/03 §3, docs/08 §1-2)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from auditguard.schemas.common import Model, UtcDatetime

Sha256Hex = str  # 64 lowercase hex characters; enforced by Field patterns below
_SHA256 = r"^[0-9a-f]{64}$"


class Payload(Model):
    report_version: int = Field(ge=1)
    canonical_json: str
    sha256: Sha256Hex = Field(pattern=_SHA256)


class ApprovalRecord(Model):
    approval_id: str
    case_id: str
    payload_sha256: Sha256Hex = Field(pattern=_SHA256)
    report_version: int = Field(ge=1)
    requester_id: str
    approver_id: str
    role: Literal["principal_officer"]
    decision: Literal["approve", "edit", "return", "reject"]
    expires_at: UtcDatetime
    hmac: str
    void: bool = False


class Receipt(Model):
    ack_id: str
    case_id: str
    payload_sha256: Sha256Hex = Field(pattern=_SHA256)
    ts: UtcDatetime
