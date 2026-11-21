"""R07 fan-in. Defaults: S = 20 distinct senders, W = 7 days."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from auditguard.schemas import RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import LedgerDefectError
from mcp_servers.aml_engine.engine.rules.r07 import R07Params, evaluate
from tests.rules.helpers import IST, pack, txn

PARAMS = pack().rules["R07"].params
assert isinstance(PARAMS, R07Params)


def run(ledger: list[Transaction]) -> RuleResult:
    return evaluate(ledger, None, PARAMS, IST)


def senders(n: int, *, day_of: int | None = None, start: int = 0) -> list[Transaction]:
    return [
        txn(
            "1500.00",
            "credit",
            day=day_of if day_of is not None else i % 7,
            cp=f"CPTY-SYN-{start + i:06d}",
        )
        for i in range(n)
    ]


def test_fires_on_twenty_distinct_senders_in_a_week() -> None:
    result = run(senders(23))
    assert result.fired is True
    assert result.inputs["distinct_senders_in_window"] == "23"
    assert result.score == Decimal("1.1500")
    assert len(result.supporting_txn_ids) == 23


def test_does_not_fire_below_twenty() -> None:
    result = run(senders(19))
    assert result.fired is False
    assert result.score == Decimal("0.9500")


def test_repeat_sender_counts_once() -> None:
    ledger = [*senders(19), txn("100.00", "credit", day=1, cp="CPTY-SYN-000000")]
    result = run(ledger)
    assert result.inputs["distinct_senders_in_window"] == "19"
    assert result.inputs["credits_in_window"] == "20"
    assert result.fired is False


def test_boundary_seven_day_window_inclusive() -> None:
    inside = [*senders(19, day_of=0), txn("1.00", "credit", day=6, cp="CPTY-SYN-900000")]
    outside = [*senders(19, day_of=0), txn("1.00", "credit", day=7, cp="CPTY-SYN-900000")]
    assert run(inside).fired is True
    assert run(inside).inputs["window_end"] == "2026-03-07"
    assert run(outside).fired is False


def test_window_uses_kolkata_calendar_days() -> None:
    # 2026-03-07 23:30 IST is still day 7 of the window that starts 1 March locally,
    # although it is 18:00 UTC; a UTC-day window would give the same answer here, but
    # 2026-03-08 00:30 IST (19:00 UTC on 7 March) must fall outside.
    base = senders(19, day_of=0)
    late = txn("1.00", "credit", at=datetime(2026, 3, 7, 23, 30, tzinfo=IST), cp="CPTY-SYN-900001")
    next_day = txn(
        "1.00", "credit", at=datetime(2026, 3, 8, 0, 30, tzinfo=IST), cp="CPTY-SYN-900002"
    )
    assert run([*base, late]).fired is True
    assert run([*base, next_day]).fired is False


def test_debits_and_cash_without_counterparty_ignored() -> None:
    ledger = [
        *senders(18),
        txn("100.00", "debit", day=1, cp="CPTY-SYN-800000"),
        txn("100.00", "credit", channel="CASH", day=1, cp=None),
    ]
    assert run(ledger).inputs["distinct_senders_in_window"] == "18"


def test_picks_the_busiest_window() -> None:
    early = senders(5, day_of=0)
    late = senders(20, day_of=20, start=100)
    result = run([*early, *late])
    assert result.fired is True
    assert result.inputs["window_start"] == "2026-03-21"


def test_empty_ledger() -> None:
    result = run([])
    assert result.fired is False
    assert result.inputs["distinct_senders_in_window"] == "0"
    assert result.supporting_txn_ids == []


def test_data_defect_duplicate_txn_id_rejected() -> None:
    a = txn("1.00", "credit", txn_id="TXN-SYN-9100001")
    with pytest.raises(LedgerDefectError):
        run([a, a])
