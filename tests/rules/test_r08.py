"""R08 fan-out. Defaults: R = 20 distinct beneficiaries, W = 7 days."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auditguard.schemas import RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import LedgerDefectError
from mcp_servers.aml_engine.engine.rules.r08 import R08Params, evaluate
from tests.rules.helpers import IST, pack, txn

PARAMS = pack().rules["R08"].params
assert isinstance(PARAMS, R08Params)


def run(ledger: list[Transaction]) -> RuleResult:
    return evaluate(ledger, None, PARAMS, IST)


def payees(n: int, *, day_of: int | None = None) -> list[Transaction]:
    return [
        txn("2500.00", "debit", day=day_of if day_of is not None else i % 7, cp=f"CPTY-SYN-{i:06d}")
        for i in range(n)
    ]


def test_fires_on_twenty_distinct_beneficiaries_in_a_week() -> None:
    result = run(payees(25))
    assert result.fired is True
    assert result.inputs["distinct_beneficiaries_in_window"] == "25"
    assert result.score == Decimal("1.2500")


def test_does_not_fire_below_twenty() -> None:
    result = run(payees(19))
    assert result.fired is False
    assert result.score == Decimal("0.9500")


def test_credits_and_cash_are_not_beneficiaries() -> None:
    ledger = [*payees(19), txn("1.00", "credit", cp="CPTY-SYN-800000")]
    ledger.append(txn("500.00", "debit", channel="CASH", cp=None))
    assert run(ledger).fired is False


def test_boundary_seven_day_window_inclusive() -> None:
    inside = [*payees(19, day_of=0), txn("1.00", "debit", day=6, cp="CPTY-SYN-900000")]
    outside = [*payees(19, day_of=0), txn("1.00", "debit", day=7, cp="CPTY-SYN-900000")]
    assert run(inside).fired is True
    assert run(outside).fired is False


def test_empty_ledger() -> None:
    result = run([])
    assert result.fired is False
    assert result.inputs["window_start"] == ""


def test_mixed_currency_is_a_defect() -> None:
    with pytest.raises(LedgerDefectError, match="non-INR"):
        run([txn("1.00", "debit", currency="USD")])
