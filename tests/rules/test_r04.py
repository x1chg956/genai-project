"""R04 rapid pass-through. Defaults: P = 0.90, H = 48 hours, residual <= 1000.00."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auditguard.schemas import RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import LedgerDefectError
from mcp_servers.aml_engine.engine.rules.r04 import R04Params, evaluate
from tests.rules.helpers import IST, pack, txn

PARAMS = pack().rules["R04"].params
assert isinstance(PARAMS, R04Params)


def run(ledger: list[Transaction]) -> RuleResult:
    return evaluate(ledger, None, PARAMS, IST)


def test_fires_when_inflows_leave_within_hours() -> None:
    ledger = [
        txn("40000.00", "credit", hours=0, cp="CPTY-SYN-000010"),
        txn("35000.00", "credit", hours=2, cp="CPTY-SYN-000011"),
        txn("74500.00", "debit", hours=20, cp="CPTY-SYN-000099"),
    ]
    result = run(ledger)
    assert result.fired is True
    assert result.inputs["total_credits"] == "75000.00"
    assert result.inputs["debited_within_hold"] == "74500.00"
    assert result.inputs["residual_balance"] == "500.00"
    assert result.inputs["share_out"] == "0.9933"
    assert set(result.supporting_txn_ids) == {t.txn_id for t in ledger}


def test_does_not_fire_when_money_stays() -> None:
    ledger = [txn("50000.00", "credit"), txn("5000.00", "debit", hours=5)]
    result = run(ledger)
    assert result.fired is False
    assert result.inputs["residual_balance"] == "45000.00"


def test_does_not_fire_when_money_leaves_slowly() -> None:
    ledger = [txn("50000.00", "credit"), txn("50000.00", "debit", hours=49)]
    result = run(ledger)
    assert result.fired is False
    assert result.inputs["debited_within_hold"] == "0.00"
    assert result.inputs["residual_balance"] == "0.00"


def test_boundary_exactly_48_hours_counts() -> None:
    at_h = run([txn("50000.00", "credit"), txn("50000.00", "debit", hours=48)])
    just_after = run([txn("50000.00", "credit"), txn("50000.00", "debit", hours=48 + 1 / 3600)])
    assert at_h.fired is True
    assert just_after.fired is False


def test_boundary_share_exactly_p_fires_and_just_below_does_not() -> None:
    # 9000.00 of 10000.00 = exactly 0.90; residual 1000.00 = exactly the maximum.
    exact = run([txn("10000.00", "credit"), txn("9000.00", "debit", hours=1)])
    # 8999.99 of 10000.00 = 0.899999: rounds to 0.9000 for display but must not fire.
    below = run([txn("10000.00", "credit"), txn("8999.99", "debit", hours=1)])
    assert exact.fired is True
    assert exact.inputs["residual_balance"] == "1000.00"
    assert below.fired is False
    assert below.inputs["share_out"] == "0.9000"  # display rounding only


def test_boundary_residual_above_max_blocks() -> None:
    result = run([txn("100000.00", "credit"), txn("98999.99", "debit", hours=1)])
    assert Decimal(result.inputs["share_out"]) >= Decimal("0.90")
    assert result.inputs["residual_balance"] == "1000.01"
    assert result.fired is False


def test_fifo_matching_across_credits() -> None:
    # Old credit (day 0) is consumed first by the day-3 debit, so it does not count as fast.
    ledger = [
        txn("10000.00", "credit", day=0),
        txn("10000.00", "credit", day=3, hours=0),
        txn("20000.00", "debit", day=3, hours=1),
    ]
    result = run(ledger)
    assert result.inputs["debited_within_hold"] == "10000.00"
    assert result.inputs["share_out"] == "0.5000"
    assert result.fired is False


def test_same_instant_credit_is_processed_first() -> None:
    ledger = [
        txn("5000.00", "debit", hours=0, txn_id="TXN-SYN-0000001"),
        txn("5000.00", "credit", hours=0, txn_id="TXN-SYN-0000002"),
    ]
    assert run(ledger).inputs["debited_within_hold"] == "5000.00"


def test_debit_before_any_credit_is_not_matched() -> None:
    ledger = [txn("5000.00", "debit", hours=0), txn("5000.00", "credit", hours=1)]
    result = run(ledger)
    assert result.inputs["debited_within_hold"] == "0.00"
    assert result.inputs["residual_balance"] == "5000.00"


def test_empty_ledger() -> None:
    result = run([])
    assert result.fired is False
    assert result.score == Decimal("0")
    assert result.inputs["total_credits"] == "0.00"


def test_debits_only_does_not_fire() -> None:
    assert run([txn("5000.00", "debit")]).fired is False


def test_data_defect_mixed_accounts_rejected() -> None:
    with pytest.raises(LedgerDefectError, match="mixes accounts"):
        run([txn("1.00", "credit"), txn("1.00", "debit", account="ACCT-SYN-000002")])
