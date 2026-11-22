"""Ledger data quality (mcp_servers/common/quality.py): quarantine, netting, reconciliation.

Every balance below is worked out by hand; amounts are exact Decimal strings.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from mcp_servers.common.ledger import booked_balances
from mcp_servers.common.quality import assess, clean, rule_input, unreconciled_days
from tests.mcp_servers.helpers import ist, txn

IST = ZoneInfo("Asia/Kolkata")


def d(day: int) -> date:
    return date(2026, 3, day)


def bal(**days: str) -> dict[date, Decimal]:
    """bal(d28="100.00") → {2026-03-28: Decimal("100.00")}."""
    return {d(int(k[1:])): Decimal(v) for k, v in days.items()}


A = txn(1, ist(29, 10), "100.00", "credit", "UPI", "CPTY-SYN-000001")
B = txn(2, ist(29, 11), "40.00", "debit", "UPI", "CPTY-SYN-000002")
C = txn(3, ist(30, 12), "25.50", "debit", "CARD", "CPTY-SYN-000003")


def test_exact_copy_is_quarantined_and_lowest_id_kept() -> None:
    copy = B.model_copy(update={"txn_id": "TXN-SYN-0000009"})
    result = clean([copy, A, B])
    assert [q.model_dump() for q in result.quarantined] == [
        {"txn_id": "TXN-SYN-0000009", "duplicate_of": B.txn_id}
    ]
    assert [t.txn_id for t in result.posted] == [A.txn_id, B.txn_id]


def test_same_amount_a_second_apart_is_not_a_duplicate() -> None:
    later = B.model_copy(update={"txn_id": "TXN-SYN-0000009", "ts": ist(29, 11, 1)})
    assert clean([A, B, later]).quarantined == ()


def test_matched_reversal_pair_is_netted_out_of_rule_input() -> None:
    back = txn(4, ist(29, 13), "40.00", "credit", "UPI", "CPTY-SYN-000002").model_copy(
        update={"reverses_txn_id": B.txn_id}
    )
    result = clean([A, B, C, back])
    assert [n.model_dump() for n in result.netted] == [
        {"txn_id": B.txn_id, "reversal_txn_id": back.txn_id, "amount": Decimal("40.00")}
    ]
    assert [t.txn_id for t in rule_input([A, B, C, back])] == [A.txn_id, C.txn_id]
    assert len(result.posted) == 4  # both entries really posted


def test_reversal_that_does_not_match_is_kept_and_reported() -> None:
    partial = txn(4, ist(29, 13), "39.99", "credit", "UPI").model_copy(
        update={"reverses_txn_id": B.txn_id}
    )
    orphan = txn(5, ist(29, 14), "10.00", "credit", "UPI").model_copy(
        update={"reverses_txn_id": "TXN-SYN-0000777"}
    )
    same_direction = txn(6, ist(29, 15), "40.00", "debit", "UPI").model_copy(
        update={"reverses_txn_id": B.txn_id}
    )
    result = clean([A, B, partial, orphan, same_direction])
    assert result.netted == ()
    assert result.unmatched_reversals == (partial.txn_id, orphan.txn_id, same_direction.txn_id)
    assert len(result.rule_input) == 5


def test_a_txn_is_netted_at_most_once() -> None:
    back1 = txn(4, ist(29, 13), "40.00", "credit", "UPI").model_copy(
        update={"reverses_txn_id": B.txn_id}
    )
    back2 = txn(5, ist(29, 14), "40.00", "credit", "UPI").model_copy(
        update={"reverses_txn_id": B.txn_id}
    )
    result = clean([B, back1, back2])
    assert [n.reversal_txn_id for n in result.netted] == [back1.txn_id]
    assert result.unmatched_reversals == (back2.txn_id,)


def test_every_day_reconciles_when_balances_follow_the_rows() -> None:
    # 28: 500.00 → 29: +100.00 -40.00 = 560.00 → 30: -25.50 = 534.50 → 31: quiet
    closing = bal(d28="500.00", d29="560.00", d30="534.50", d31="534.50")
    assert unreconciled_days([A, B, C], closing, d(29), d(31), IST) == (3, [])


def test_missing_rows_show_as_an_unreconciled_day() -> None:
    closing = bal(d28="500.00", d29="560.00", d30="534.50", d31="534.50")
    # C (30 March) never reached the feed: 30 March moved -25.50 with no row
    assert unreconciled_days([A, B], closing, d(29), d(31), IST) == (3, [d(30)])


def test_day_without_a_balance_on_file_is_unreconciled() -> None:
    closing = bal(d28="500.00", d29="560.00", d31="534.50")
    assert unreconciled_days([A, B, C], closing, d(29), d(31), IST) == (3, [d(30), d(31)])


def test_days_before_opening_are_not_expected_and_opening_day_starts_at_zero() -> None:
    closing = bal(d29="60.00", d30="34.50")
    assert unreconciled_days([A, B, C], closing, d(25), d(30), IST, opened_on=d(29)) == (2, [])


def test_duplicate_does_not_break_reconciliation_but_reversal_counts() -> None:
    copy = B.model_copy(update={"txn_id": "TXN-SYN-0000009"})
    back = txn(4, ist(29, 13), "40.00", "credit", "UPI").model_copy(
        update={"reverses_txn_id": B.txn_id}
    )
    # books: 29 March = +100.00 -40.00 +40.00 (reversal) = +100.00; the copy was never booked
    closing = bal(d28="500.00", d29="600.00")
    q = assess([A, B, copy, back], closing, d(29), d(29), IST)
    assert (q.days_expected, q.days_reconciled, q.unreconciled_days) == (1, 1, [])
    assert (len(q.quarantined), len(q.netted), q.rule_input_count) == (1, 1, 1)


def test_booked_balances_helper_matches_hand_arithmetic() -> None:
    out = booked_balances([A, B, C], IST)["ACCT-SYN-000001"]
    assert out == bal(d28="0.00", d29="60.00", d30="34.50")


def test_empty_slice_with_balances_reconciles_quiet_days() -> None:
    closing = bal(d28="10.00", d29="10.00")
    q = assess([], closing, d(29), d(29), IST)
    assert (q.days_reconciled, q.rule_input_count) == (1, 0)
