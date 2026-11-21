from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from auditguard.ledger_ref import LedgerRefError, ledger_digest, make_ledger_ref, parse_ledger_ref
from auditguard.schemas import Transaction


def txn(i: int, amount: str = "100.00") -> Transaction:
    return Transaction(
        txn_id=f"TXN-SYN-{i:07d}",
        account_token="ACCT-SYN-000001",
        ts=datetime(2026, 3, 1, 5, i, tzinfo=UTC),
        amount=Decimal(amount),
        direction="credit",
        channel="UPI",
    )


def test_round_trip() -> None:
    ref = make_ledger_ref("ACCT-SYN-000001", date(2026, 3, 1), date(2026, 3, 31), [txn(1)])
    assert parse_ledger_ref(str(ref)) == ref


def test_digest_ignores_order_but_not_content() -> None:
    assert ledger_digest([txn(1), txn(2)]) == ledger_digest([txn(2), txn(1)])
    assert ledger_digest([txn(1)]) != ledger_digest([txn(1, "100.01")])
    assert ledger_digest([txn(1)]) != ledger_digest([txn(1), txn(2)])


@pytest.mark.parametrize(
    "bad",
    [
        "ledger:v2:A:2026-03-01:2026-03-31:0123456789abcdef",
        "ledger:v1:A:2026-03-31:2026-03-01:0123456789abcdef",
        "ledger:v1:A:2026-3-1:2026-03-31:0123456789abcdef",
        "ledger:v1:A:2026-03-01:2026-03-31:XYZ",
        "nonsense",
    ],
)
def test_parse_rejects(bad: str) -> None:
    with pytest.raises(LedgerRefError):
        parse_ledger_ref(bad)
