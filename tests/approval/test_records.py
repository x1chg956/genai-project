from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from auditguard.approval.records import ApprovalStore, hmac_valid, problems, signed
from auditguard.schemas import ApprovalRecord

KEY = b"test-approval-hmac-key"
NOW = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
H = "a" * 64


def record(**over: object) -> ApprovalRecord:
    base = ApprovalRecord(
        approval_id="APR-1",
        case_id="ALR-SYN-0001",
        payload_sha256=H,
        report_version=1,
        requester_id="analyst-1",
        approver_id="po-1",
        role="principal_officer",
        decision="approve",
        expires_at=NOW + timedelta(minutes=120),
        hmac="",
    )
    return signed(base.model_copy(update=over), KEY)


def test_valid_record_has_no_problems() -> None:
    assert problems(record(), case_id="ALR-SYN-0001", key=KEY, now=NOW) == []


def test_tampered_field_breaks_the_hmac() -> None:
    tampered = record().model_copy(update={"payload_sha256": "b" * 64})
    assert not hmac_valid(tampered, KEY)
    assert "approval signature is invalid" in problems(
        tampered, case_id="ALR-SYN-0001", key=KEY, now=NOW
    )


def test_wrong_key_breaks_the_hmac() -> None:
    assert not hmac_valid(record(), b"other-key")


def test_void_does_not_break_the_signature_but_blocks_use() -> None:
    voided = record().model_copy(update={"void": True})
    assert hmac_valid(voided, KEY)
    assert problems(voided, case_id="ALR-SYN-0001", key=KEY, now=NOW) == [
        "approval was voided by a later edit"
    ]


def test_each_rule_reported() -> None:
    assert problems(record(approver_id="analyst-1"), case_id="ALR-SYN-0001", key=KEY, now=NOW) == [
        "approver is the requester (self-approval)"
    ]
    assert problems(record(), case_id="ALR-SYN-0001", key=KEY, now=NOW + timedelta(hours=2)) == [
        "approval has expired"
    ]
    assert problems(record(decision="reject"), case_id="ALR-SYN-0001", key=KEY, now=NOW) == [
        "decision is 'reject', not 'approve'"
    ]
    assert problems(record(), case_id="ALR-SYN-0002", key=KEY, now=NOW) == [
        "approval belongs to another case"
    ]


def test_store_round_trip_and_void(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path / "a.db")
    store.put(record())
    assert store.get("APR-1") == record()
    assert store.void_case("ALR-SYN-0001") == 1
    got = store.get("APR-1")
    assert got is not None and got.void
    reader = ApprovalStore(tmp_path / "a.db", read_only=True)
    got_ro = reader.get("APR-1")
    assert got_ro is not None and got_ro.void
    assert reader.get("missing") is None


def test_read_only_store_before_any_approval(tmp_path: Path) -> None:
    assert ApprovalStore(tmp_path / "none.db", read_only=True).get("APR-1") is None
