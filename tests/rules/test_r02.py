"""R02 integrally connected cash. Default: T = 10,00,000.00 (placeholder), calendar months IST."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from auditguard.schemas import RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import LedgerDefectError
from mcp_servers.aml_engine.engine.rules.r02 import R02Params, evaluate
from tests.rules.helpers import IST, pack, txn

PARAMS = pack().rules["R02"].params
assert isinstance(PARAMS, R02Params)


def run(ledger: list[Transaction]) -> RuleResult:
    return evaluate(ledger, None, PARAMS, IST)


def cash(amount: str, day: int, direction: str = "credit") -> Transaction:
    return txn(amount, direction, channel="CASH", day=day, cp=None)  # type: ignore[arg-type]


def test_fires_when_a_month_of_small_cash_reaches_t() -> None:
    result = run([cash("400000.00", 0), cash("350000.00", 5), cash("250000.00", 10)])
    assert result.fired is True
    assert result.inputs["month_total"] == "1000000.00"
    assert result.inputs["month"] == "2026-03"
    assert result.score == Decimal("1.0000")


def test_does_not_fire_one_paisa_short() -> None:
    result = run([cash("400000.00", 0), cash("350000.00", 5), cash("249999.99", 10)])
    assert result.fired is False
    assert result.inputs["month_total"] == "999999.99"


def test_a_single_deposit_of_t_or_more_is_excluded() -> None:
    result = run([cash("1000000.00", 0), cash("10.00", 1)])
    assert result.inputs["month_total"] == "10.00"
    assert result.fired is False


def test_calendar_month_boundary_in_ist() -> None:
    # 31 Mar 23:30 IST and 1 Apr 00:30 IST fall in different months.
    late = txn("600000.00", channel="CASH", cp=None, at=datetime(2026, 3, 31, 23, 30, tzinfo=IST))
    early = txn("600000.00", channel="CASH", cp=None, at=datetime(2026, 4, 1, 0, 30, tzinfo=IST))
    assert run([late, early]).fired is False
    assert run([late, early]).inputs["month_total"] == "600000.00"


def test_deposits_and_withdrawals_are_not_netted_or_mixed() -> None:
    result = run([cash("600000.00", 0), cash("600000.00", 2, "debit")])
    assert result.fired is False
    result = run([cash("600000.00", 0, "debit"), cash("600000.00", 2, "debit")])
    assert result.fired is True
    assert result.inputs["direction"] == "debit"


def test_non_cash_is_ignored() -> None:
    assert run([txn("900000.00", day=0), txn("900000.00", day=1)]).fired is False


def test_empty_ledger() -> None:
    result = run([])
    assert result.fired is False
    assert result.inputs["month_total"] == "0.00"
    assert result.supporting_txn_ids == []


def test_duplicate_txn_is_a_defect() -> None:
    t = cash("10.00", 0)
    with pytest.raises(LedgerDefectError, match="duplicate"):
        run([t, t])
