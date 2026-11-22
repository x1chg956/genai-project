"""R03 velocity spike. Defaults: k = 3, W = 7-day blocks, at least 4 baseline blocks."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auditguard.schemas import RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import LedgerDefectError
from mcp_servers.aml_engine.engine.rules.r03 import R03Params, evaluate, p90
from tests.rules.helpers import IST, pack, txn

PARAMS = pack().rules["R03"].params
assert isinstance(PARAMS, R03Params)


def run(ledger: list[Transaction]) -> RuleResult:
    return evaluate(ledger, None, PARAMS, IST)


def steady(weeks: int, per_week: int = 2, amount: str = "1000.00") -> list[Transaction]:
    return [txn(amount, "debit", day=w * 7 + i) for w in range(weeks) for i in range(per_week)]


def test_p90_nearest_rank() -> None:
    assert p90([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 9
    assert p90([5]) == 5
    assert p90([Decimal("1.50"), Decimal("0.10")]) == Decimal("1.50")


def test_fires_on_a_count_spike() -> None:
    ledger = [*steady(8), *[txn("1000.00", "debit", day=8 * 7 + i % 7) for i in range(7)]]
    result = run(ledger)
    assert result.fired is True
    assert result.inputs["block_count"] == "7"
    assert result.inputs["baseline_p90_count"] == "2"
    assert result.score == Decimal("1.1667")  # 7 / (3 x 2)


def test_fires_on_a_value_spike() -> None:
    ledger = [*steady(8), txn("6000.01", "debit", day=8 * 7)]
    result = run(ledger)
    assert result.fired is True
    assert result.inputs["block_value"] == "6000.01"
    assert result.inputs["baseline_p90_value"] == "2000.00"


def test_exactly_k_times_does_not_fire() -> None:
    ledger = [*steady(8), *[txn("1000.00", "debit", day=8 * 7 + i) for i in range(6)]]
    result = run(ledger)  # 6 = 3 x 2 and 6000.00 = 3 x 2000.00: strict comparison
    assert result.fired is False
    assert result.score == Decimal("1.0000")


def test_too_little_history_does_not_fire() -> None:
    ledger = [*steady(3), *[txn("1000.00", "debit", day=21 + i % 7) for i in range(30)]]
    result = run(ledger)  # 4 blocks: only 3 others, below min_baseline_blocks = 4
    assert result.fired is False
    assert result.inputs["blocks_in_slice"] == "4"


def test_steady_activity_does_not_fire() -> None:
    assert run(steady(10)).fired is False


def test_empty_ledger() -> None:
    result = run([])
    assert result.fired is False
    assert result.inputs["blocks_in_slice"] == "0"


def test_duplicate_is_a_defect() -> None:
    t = txn("1.00")
    with pytest.raises(LedgerDefectError):
        run([t, t])
