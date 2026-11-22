"""Ledger data quality, shared by the transactions and aml_engine servers (docs/03 §2 step 4).

One definition of the cleaned slice, so every computed number agrees with the rules:

- **Duplicate postings** are quarantined: rows of one account with the same time, amount,
  direction, channel, counterparty and reversal reference as an earlier row (lowest txn_id
  kept). Only exact copies count; two genuine payments a second apart both stay.
- **Reversals** are netted: a row whose structured `reverses_txn_id` names a row in the
  slice, of the same account and amount and the opposite direction, at or after it. Both
  rows leave the rule input (the money never moved). The memo text is never read: it is
  untrusted data. A reversal that does not match is kept and reported.
- **Missing days** are found by reconciling against the bank's end-of-day closing balances:
  local day d reconciles when closing(d) - closing(d-1) equals the day's posted credits
  minus debits (duplicates removed, reversals included: both really posted). A day with no
  balance on file, or whose movement disagrees, is unreconciled: rows are missing or extra.
  A day before the account opened is not expected. A gap whose missing rows net to exactly
  zero cannot be seen this way.

Pure: no I/O. Money is Decimal throughout.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import Field

from auditguard.schemas import Model, Transaction
from auditguard.schemas.common import Money

ZERO = Decimal("0.00")


class QuarantinedDuplicate(Model):
    txn_id: str
    duplicate_of: str


class NettedReversal(Model):
    txn_id: str
    reversal_txn_id: str
    amount: Money


class LedgerQuality(Model):
    """Data quality of one account's slice between two local dates, inclusive."""

    days_expected: int = Field(ge=0)
    days_reconciled: int = Field(ge=0)
    unreconciled_days: list[date]
    quarantined: list[QuarantinedDuplicate]
    netted: list[NettedReversal]
    unmatched_reversals: list[str]
    rule_input_count: int = Field(ge=0)
    """Rows left after quarantine and netting: what evaluate_rules and the summaries use."""


@dataclass(frozen=True)
class Cleaned:
    posted: tuple[Transaction, ...]  # duplicates removed: what the bank really booked
    rule_input: tuple[Transaction, ...]  # posted minus netted reversal pairs
    quarantined: tuple[QuarantinedDuplicate, ...]
    netted: tuple[NettedReversal, ...]
    unmatched_reversals: tuple[str, ...]


def _content(t: Transaction) -> tuple[object, ...]:
    return (
        t.account_token,
        t.ts,
        t.amount,
        t.currency,
        t.direction,
        t.channel,
        t.counterparty_token,
        t.reverses_txn_id,
    )


def clean(rows: Sequence[Transaction]) -> Cleaned:
    """Quarantine exact duplicate postings, then net matched reversal pairs."""
    kept: dict[tuple[object, ...], Transaction] = {}
    quarantined: list[QuarantinedDuplicate] = []
    for t in sorted(rows, key=lambda r: r.txn_id):
        first = kept.get(_content(t))
        if first is None:
            kept[_content(t)] = t
        else:
            quarantined.append(QuarantinedDuplicate(txn_id=t.txn_id, duplicate_of=first.txn_id))
    posted = sorted(kept.values(), key=lambda r: (r.ts, r.txn_id))

    by_id = {t.txn_id: t for t in posted}
    netted: list[NettedReversal] = []
    unmatched: list[str] = []
    gone: set[str] = set()
    for t in posted:
        if t.reverses_txn_id is None:
            continue
        original = by_id.get(t.reverses_txn_id)
        if (
            original is None
            or original.txn_id in gone
            or original.reverses_txn_id is not None
            or original.account_token != t.account_token
            or original.amount != t.amount
            or original.direction == t.direction
            or original.ts > t.ts
        ):
            unmatched.append(t.txn_id)
            continue
        gone.update((original.txn_id, t.txn_id))
        netted.append(
            NettedReversal(txn_id=original.txn_id, reversal_txn_id=t.txn_id, amount=t.amount)
        )
    rule_input = tuple(t for t in posted if t.txn_id not in gone)
    return Cleaned(tuple(posted), rule_input, tuple(quarantined), tuple(netted), tuple(unmatched))


def rule_input(rows: Sequence[Transaction]) -> list[Transaction]:
    """The cleaned rows every computation uses, ordered by (ts, txn_id)."""
    return list(clean(rows).rule_input)


def unreconciled_days(
    posted: Sequence[Transaction],
    closing: Mapping[date, Decimal],
    from_date: date,
    to_date: date,
    tz: ZoneInfo,
    opened_on: date | None = None,
) -> tuple[int, list[date]]:
    """(days expected, days whose closing-balance movement disagrees with the posted rows).

    `closing` must hold the day before `from_date` too, as the opening balance. On the day
    the account opened, the opening balance is zero.
    """
    net: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for t in posted:
        day = t.ts.astimezone(tz).date()
        net[day] += t.amount if t.direction == "credit" else -t.amount
    first = max(from_date, opened_on) if opened_on else from_date
    expected = 0
    bad: list[date] = []
    day = first
    while day <= to_date:
        expected += 1
        before = ZERO if day == opened_on else closing.get(day - timedelta(days=1))
        after = closing.get(day)
        if before is None or after is None or after - before != net[day]:
            bad.append(day)
        day += timedelta(days=1)
    return expected, bad


def assess(
    rows: Sequence[Transaction],
    closing: Mapping[date, Decimal],
    from_date: date,
    to_date: date,
    tz: ZoneInfo,
    opened_on: date | None = None,
) -> LedgerQuality:
    cleaned = clean(rows)
    expected, bad = unreconciled_days(cleaned.posted, closing, from_date, to_date, tz, opened_on)
    return LedgerQuality(
        days_expected=expected,
        days_reconciled=expected - len(bad),
        unreconciled_days=bad,
        quarantined=list(cleaned.quarantined),
        netted=list(cleaned.netted),
        unmatched_reversals=list(cleaned.unmatched_reversals),
        rule_input_count=len(cleaned.rule_input),
    )
