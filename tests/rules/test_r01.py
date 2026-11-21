"""R01 cash structuring. Defaults: T = 1000000.00, L = 800000.00, N = 3, W = 30 days."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from auditguard.schemas import RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import LedgerDefectError
from mcp_servers.aml_engine.engine.rules.r01 import R01Params, evaluate
from tests.rules.helpers import IST, pack, txn

PARAMS = pack().rules["R01"].params
assert isinstance(PARAMS, R01Params)


def cash(amount: str, day: int, **kw: Any) -> Transaction:
    return txn(amount, "credit", channel="CASH", day=day, cp=None, **kw)


def run(ledger: list[Transaction]) -> RuleResult:
    return evaluate(ledger, None, PARAMS, IST)


def test_params_from_yaml() -> None:
    assert PARAMS.threshold == Decimal("1000000.00")
    assert PARAMS.lower_bound == Decimal("800000.00")
    assert (PARAMS.min_count, PARAMS.window_days) == (3, 30)


def test_fires_on_three_deposits_just_below_threshold() -> None:
    ledger = [cash("950000.00", 0), cash("990000.00", 5), cash("900000.00", 12)]
    result = run(ledger)
    assert result.fired is True
    assert result.inputs["qualifying_deposits_in_window"] == "3"
    assert result.inputs["total_in_window"] == "2840000.00"
    assert result.score == Decimal("1.0000")
    assert result.supporting_txn_ids == [t.txn_id for t in ledger]


def test_does_not_fire_with_two_deposits() -> None:
    result = run([cash("950000.00", 0), cash("950000.00", 3)])
    assert result.fired is False
    assert result.score == Decimal("0.6667")


def test_boundary_amounts_lower_inclusive_threshold_exclusive() -> None:
    at_l = run([cash("800000.00", 0), cash("800000.00", 1), cash("800000.00", 2)])
    below_l = run([cash("799999.99", 0), cash("800000.00", 1), cash("800000.00", 2)])
    at_t = run([cash("1000000.00", 0), cash("999999.99", 1), cash("999999.99", 2)])
    assert at_l.fired is True
    assert below_l.fired is False
    assert at_t.fired is False  # a deposit of exactly T is reportable, not structuring
    assert at_t.inputs["qualifying_deposits_in_window"] == "2"


def test_boundary_window_30_days_inclusive() -> None:
    inside = run([cash("900000.00", 0), cash("900000.00", 10), cash("900000.00", 29)])
    outside = run([cash("900000.00", 0), cash("900000.00", 10), cash("900000.00", 30)])
    assert inside.fired is True
    assert inside.inputs["window_end"] == "2026-03-30"
    assert outside.fired is False


def test_window_uses_kolkata_calendar_days() -> None:
    # 2026-03-01 00:10 IST is 2026-02-28 18:40 UTC: it belongs to 1 March locally.
    first = cash("900000.00", 0, at=datetime(2026, 3, 1, 0, 10, tzinfo=IST))
    last = cash("900000.00", 0, at=datetime(2026, 3, 30, 23, 50, tzinfo=IST))
    result = run([first, cash("900000.00", 5), last])
    assert result.fired is True
    assert result.inputs["window_start"] == "2026-03-01"


def test_ignores_non_cash_and_debits() -> None:
    ledger = [
        txn("900000.00", "credit", channel="NEFT", day=0),
        txn("900000.00", "debit", channel="CASH", day=1, cp=None),
        cash("900000.00", 2),
    ]
    assert run(ledger).inputs["qualifying_deposits_in_ledger"] == "1"


def test_empty_ledger() -> None:
    result = run([])
    assert result.fired is False
    assert result.score == Decimal("0")
    assert result.supporting_txn_ids == []
    assert result.inputs["total_in_window"] == "0.00"


def test_data_defect_duplicate_txn_id_rejected() -> None:
    a = cash("900000.00", 0, txn_id="TXN-SYN-9000001")
    with pytest.raises(LedgerDefectError, match="duplicate"):
        run([a, a, cash("900000.00", 1)])


def test_data_defect_non_inr_rejected() -> None:
    with pytest.raises(LedgerDefectError, match="non-INR"):
        run([cash("900000.00", 0, currency="USD")])


def test_result_records_version_and_thresholds() -> None:
    result = run([cash("900000.00", 0)])
    assert result.version == "1.0.0"
    assert result.explanation_template_id == "R01_v1"
    assert result.threshold == {
        "threshold": "1000000.00",
        "lower_bound": "800000.00",
        "min_count": "3",
        "window_days": "30",
    }
