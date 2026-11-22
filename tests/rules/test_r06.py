"""R06 profile mismatch. Default: m = 3 x declared monthly turnover, calendar months IST."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from auditguard.schemas import KycProfile, RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import LedgerDefectError
from mcp_servers.aml_engine.engine.rules.r06 import R06Params, evaluate
from tests.rules.helpers import IST, pack, txn

PARAMS = pack().rules["R06"].params
assert isinstance(PARAMS, R06Params)


def kyc(declared: str) -> KycProfile:
    return KycProfile(
        subject_token="CUST-SYN-000001",
        segment="salaried",
        kyc_risk_band="low",
        declared_monthly_turnover=Decimal(declared),
        onboarded_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def run(ledger: list[Transaction], declared: str | None = "50000.00") -> RuleResult:
    return evaluate(ledger, kyc(declared) if declared else None, PARAMS, IST)


def test_fires_above_three_times_declared() -> None:
    result = run([txn("100000.00", day=0), txn("50000.01", day=3)])
    assert result.fired is True
    assert result.inputs["credit_turnover"] == "150000.01"
    assert result.inputs["limit"] == "150000.00"
    assert result.score == Decimal("1.0000")


def test_exactly_three_times_does_not_fire() -> None:
    result = run([txn("100000.00", day=0), txn("50000.00", day=3)])
    assert result.fired is False


def test_debits_are_not_turnover() -> None:
    assert run([txn("900000.00", "debit")]).fired is False


def test_months_are_separate() -> None:
    a = txn("100000.00", at=datetime(2026, 3, 31, 22, 0, tzinfo=IST))
    b = txn("100000.00", at=datetime(2026, 4, 1, 9, 0, tzinfo=IST))
    result = run([a, b])
    assert result.fired is False
    assert result.inputs["credit_turnover"] == "100000.00"
    assert result.inputs["month"] == "2026-03"


def test_missing_kyc_does_not_fire_and_says_so() -> None:
    result = run([txn("900000.00")], declared=None)
    assert result.fired is False
    assert result.inputs == {"kyc_profile": "missing"}


def test_empty_ledger() -> None:
    result = run([])
    assert result.fired is False
    assert result.inputs["credit_turnover"] == "0.00"


def test_other_accounts_are_a_defect() -> None:
    with pytest.raises(LedgerDefectError, match="mixes accounts"):
        run([txn("1.00"), txn("1.00", account="ACCT-SYN-000002")])
