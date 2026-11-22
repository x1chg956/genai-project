"""R05 dormant reactivation. Defaults: D = 365 quiet days, 30-day burst, V = 5 x median."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auditguard.schemas import RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import LedgerDefectError
from mcp_servers.aml_engine.engine.rules.r05 import R05Params, evaluate, median
from tests.rules.helpers import IST, pack, txn

PARAMS = pack().rules["R05"].params
assert isinstance(PARAMS, R05Params)


def run(ledger: list[Transaction]) -> RuleResult:
    return evaluate(ledger, None, PARAMS, IST)


def history() -> list[Transaction]:
    """A year of 1,000.00 debits, then silence. Median 1,000.00, so V = 5,000.00."""
    return [txn("1000.00", "debit", day=-800 + i * 30) for i in range(12)]  # last on day -470


def test_median() -> None:
    assert median([Decimal("3"), Decimal("1"), Decimal("2")]) == Decimal("2")
    assert median([Decimal("1.00"), Decimal("2.01")]) == Decimal("1.50")  # 1.505 half-even


def test_fires_on_reactivation_above_v() -> None:
    ledger = [*history(), txn("3000.00", day=0), txn("2000.01", "debit", day=29)]
    result = run(ledger)
    assert result.fired is True
    assert result.inputs["dormant_days"] == "469"
    assert result.inputs["limit"] == "5000.00"
    assert result.inputs["reactivation_value"] == "5000.01"
    assert len(result.supporting_txn_ids) == 2


def test_exactly_v_does_not_fire_and_day_30_is_outside() -> None:
    ledger = [*history(), txn("5000.00", day=0), txn("9999.00", day=30)]
    result = run(ledger)
    assert result.fired is False
    assert result.inputs["reactivation_value"] == "5000.00"


def test_quiet_spell_boundary() -> None:
    last = txn("1000.00", day=0)
    exactly = [last, txn("90000.00", day=366)]  # 365 quiet days between
    short = [last, txn("90000.00", day=365)]  # 364 quiet days
    assert run(exactly).fired is True
    assert run(exactly).inputs["dormant_days"] == "365"
    assert run(short).fired is False
    assert run(short).inputs["dormant_days"] == ""


def test_active_account_does_not_fire() -> None:
    assert run([txn("50000.00", day=i * 10) for i in range(40)]).fired is False


def test_empty_ledger() -> None:
    result = run([])
    assert result.fired is False
    assert result.supporting_txn_ids == []


def test_duplicate_is_a_defect() -> None:
    t = txn("1.00")
    with pytest.raises(LedgerDefectError):
        run([t, t])
