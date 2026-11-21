from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from auditguard.schemas import (
    Alert,
    ApprovalRecord,
    DataQualityReport,
    Decision,
    DraftReport,
    DraftSections,
    DraftSentence,
    HandOff,
    Hypothesis,
    ToolError,
    Transaction,
)

IST = timezone(timedelta(hours=5, minutes=30))


def txn(**overrides: Any) -> Transaction:
    fields: dict[str, Any] = {
        "txn_id": "TXN-SYN-0000001",
        "account_token": "ACCT-SYN-000001",
        "ts": datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        "amount": "50000.00",
        "direction": "credit",
        "channel": "UPI",
    }
    return Transaction(**{**fields, **overrides})


def test_amount_is_exact_decimal_from_string() -> None:
    assert txn(amount="50000.10").amount == Decimal("50000.10")


def test_amount_normalised_to_two_places_without_rounding() -> None:
    assert str(txn(amount=Decimal("10.0")).amount) == "10.00"


def test_amount_rejects_float() -> None:
    with pytest.raises(ValidationError, match="never a float"):
        txn(amount=10.5)


def test_amount_rejects_sub_paisa_precision() -> None:
    with pytest.raises(ValidationError):
        txn(amount="10.005")


def test_amount_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        txn(amount="0.00")


def test_amount_serialises_as_decimal_string() -> None:
    assert '"amount":"50000.00"' in txn().model_dump_json()


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        txn(ts=datetime(2026, 3, 1, 10, 0))


def test_timestamp_normalised_to_utc() -> None:
    t = txn(ts=datetime(2026, 3, 1, 15, 30, tzinfo=IST))
    assert t.ts == datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    assert t.ts.utcoffset() == timedelta(0)


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        txn(risk_score="0.9")


def test_models_are_immutable() -> None:
    with pytest.raises(ValidationError):
        txn().amount = Decimal("1.00")  # type: ignore[misc]


def test_alert_defaults_to_india_profile() -> None:
    alert = Alert(
        alert_id="ALR-SYN-0001",
        account_ref="ACCT-SYN-000001",
        scenario_code="R04",
        detected_at=datetime(2026, 3, 2, tzinfo=UTC),
    )
    assert alert.profile == "IN"


def test_material_sentence_needs_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence ID"):
        DraftSentence(text="Inflows were received.")
    assert DraftSentence(text="This report is prepared for review.", material=False)


def test_draft_report_collects_cited_ids() -> None:
    report = DraftReport(
        version=1,
        sections=DraftSections(
            body=[DraftSentence(text="Credit observed.", evidence_ids=["TXN-SYN-0000001"])],
            grounds=[DraftSentence(text="Rule R04 fired.", evidence_ids=["RR-R04", "TXN-SYN-1"])],
        ),
    )
    assert report.cited_ids() == {"TXN-SYN-0000001", "RR-R04", "TXN-SYN-1"}


def test_decided_hypothesis_needs_evidence() -> None:
    with pytest.raises(ValidationError):
        Hypothesis(hyp_id="H1", label="payroll", kind="benign", status="refuted")
    assert Hypothesis(hyp_id="H1", label="payroll", kind="benign")  # open needs none


def test_no_file_decision_needs_reason() -> None:
    with pytest.raises(ValidationError, match="reason_code"):
        Decision(decision="no_file", decided_by="analyst-1")
    ok = Decision(
        decision="no_file", reason_code="BENIGN_PAYROLL", rationale="verified", decided_by="a"
    )
    assert ok.decision == "no_file"


def test_data_quality_coverage_bounded() -> None:
    with pytest.raises(ValidationError):
        DataQualityReport(coverage_ratio=Decimal("1.2"), verdict="ok")


def test_tool_error_codes_are_closed_set() -> None:
    with pytest.raises(ValidationError):
        ToolError(
            error_code="OOPS", message="x", retryable=False, tool_name="t", correlation_id="c"
        )


def test_handoff_defaults() -> None:
    handoff = HandOff(case_id="C1", run_id="R1")
    assert handoff.schema_version == "1"
    assert handoff.status == "ok"
    assert handoff.error is None


def test_approval_record_requires_sha256_hex() -> None:
    with pytest.raises(ValidationError):
        ApprovalRecord(
            approval_id="A1",
            case_id="C1",
            payload_sha256="not-a-hash",
            report_version=1,
            requester_id="analyst-1",
            approver_id="po-1",
            role="principal_officer",
            decision="approve",
            expires_at=datetime(2026, 3, 2, tzinfo=UTC),
            hmac="x",
        )
