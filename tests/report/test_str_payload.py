from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from auditguard.approval.canonical import payload_hash
from auditguard.config import REPO_ROOT, ConfigError, Settings
from auditguard.report.str_payload import build_str_payload, load_reason_codes
from auditguard.schemas import Alert, Decision, DraftReport, LedgerSlice, RuleResult

CODES = load_reason_codes(REPO_ROOT / "configs" / "reason_codes.yaml")
ALERT = Alert(
    alert_id="ALR-SYN-0001",
    account_ref="ACCT-SYN-000001",
    scenario_code="RPT",
    detected_at=datetime(2026, 3, 31, 18, 29, tzinfo=UTC),
)
LEDGER = LedgerSlice(
    account_tokens=["ACCT-SYN-000001"],
    from_date=date(2026, 2, 1),
    to_date=date(2026, 3, 31),
    txn_count=7,
    ref="ledger:v1:x",
)
DRAFT = DraftReport.model_validate(
    {
        "version": 1,
        "sections": {"grounds": [{"text": "R04 indicator observed.", "evidence_ids": ["R04"]}]},
    }
)
DECISION = Decision(decision="file", decided_by="ANL-SYN-0001")


def rule(rule_id: str, fired: bool) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        version="1.0.0",
        fired=fired,
        score=Decimal("1"),
        inputs={},
        threshold={},
        explanation_template_id="t",
    )


def build(settings: Settings, indicators: list[RuleResult]) -> dict[str, object]:
    return build_str_payload(
        case_id="ALR-SYN-0001",
        report_version=1,
        alert=ALERT,
        ledger=LEDGER,
        indicators=indicators,
        draft=DRAFT,
        decision=DECISION,
        report=settings.str_report,
        reason_codes=CODES,
        tz=ZoneInfo("Asia/Kolkata"),
    )


def test_payload_is_deterministic(settings: Settings) -> None:
    a = build(settings, [rule("R04", True), rule("R01", False)])
    b = build(settings, [rule("R01", False), rule("R04", True)])
    assert payload_hash(a) == payload_hash(b)


def test_only_fired_rules_become_reason_codes(settings: Settings) -> None:
    p = build(settings, [rule("R04", True), rule("R01", False)])
    assert [c["rule_id"] for c in p["reason_codes"]] == ["R04"]  # type: ignore[index]
    assert p["grounds_of_suspicion"] == "R04 indicator observed."


def test_detection_date_is_the_local_date(settings: Settings) -> None:
    p = build(settings, [])
    assert p["report_details"]["alert_detected_on"] == "2026-03-31"  # type: ignore[index]


def test_unmapped_fired_rule_is_refused(settings: Settings) -> None:
    with pytest.raises(ConfigError, match="R99"):
        build(settings, [rule("R99", True)])
