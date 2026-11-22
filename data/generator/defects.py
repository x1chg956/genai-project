"""Data-quality defects on background accounts (docs/04 §2), `full` profile only.

- missing_days: every row of an account on 2-5 consecutive local days is dropped (a feed gap);
- double_post: a UPI or card debit is posted twice, same time, amount and counterparty, with a
  new txn_id (the ledger key stays unique, so only the content repeats);
- reversal: a transfer debit is followed minutes later by a credit of the same amount from the
  same counterparty that carries the debit's txn_id in `reverses_txn_id`, as core banking
  does (the memo also says "reversal", but nothing downstream reads memo text).

Each defect is recorded in `Dataset.defects` (groundtruth.data_defects). Pure and seeded.
The bank's books differ from the feed: dropped rows were booked, double posts were not.
`plant_defects` returns both sets so the closing balances (balances.py) follow the books.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from data.generator.background import DefectSpec
from data.generator.model import DataDefect, Dataset, TxnRow

IST = ZoneInfo("Asia/Kolkata")


def _local(row: TxnRow) -> date:
    return row.txn.ts.astimezone(IST).date()


@dataclass
class FeedDifference:
    unfed: list[TxnRow] = field(default_factory=list)  # booked, missing from the feed
    unbooked: set[str] = field(default_factory=set)  # in the feed, never booked (double posts)


def plant_defects(
    ds: Dataset,
    accounts: list[str],
    spec: DefectSpec,
    rng: random.Random,
    next_txn_id: Callable[[str, int], str],
) -> FeedDifference:
    by_account: dict[str, list[TxnRow]] = {a: [] for a in accounts}
    for row in ds.transactions:
        if row.txn.account_token in by_account:
            by_account[row.txn.account_token].append(row)

    drop: set[str] = set()
    added: list[TxnRow] = []
    diff = FeedDifference()
    for account in accounts:
        rows = by_account[account]
        if not rows:
            continue
        if rng.randrange(100) < spec.missing_days_percent:
            days = rng.randint(*spec.missing_days)
            first = min(_local(r) for r in rows)
            last = max(_local(r) for r in rows)
            span = (last - first).days - days
            if span > 0:
                start = first + timedelta(days=rng.randint(0, span))
                end = start + timedelta(days=days - 1)
                gone = [r.txn.txn_id for r in rows if start <= _local(r) <= end]
                drop.update(gone)
                ds.defects.append(
                    DataDefect(
                        account,
                        "missing_days",
                        gone,
                        {"from": start.isoformat(), "to": end.isoformat(), "days": str(days)},
                    )
                )
        for row in rows:
            t = row.txn
            if (
                t.txn_id not in drop
                and t.direction == "debit"
                and t.channel in ("UPI", "CARD")
                and rng.randrange(1000) < spec.double_post_per_mille
            ):
                copy = t.model_copy(update={"txn_id": next_txn_id("TXN", 7)})
                copy = copy.model_copy(
                    update={"memo_ref": f"MEMO-{copy.txn_id}" if row.memo else None}
                )
                added.append(TxnRow(copy, row.memo))
                diff.unbooked.add(copy.txn_id)
                ds.defects.append(
                    DataDefect(
                        account, "double_post", [t.txn_id, copy.txn_id], {"amount": str(t.amount)}
                    )
                )
        if rng.randrange(100) < spec.reversal_percent:
            transfers = [
                r
                for r in rows
                if r.txn.direction == "debit"
                and r.txn.channel in ("UPI", "IMPS")
                and r.txn.counterparty_token
                and r.txn.txn_id not in drop
            ]
            for row in rng.sample(transfers, k=min(len(transfers), rng.randint(*spec.reversals))):
                t = row.txn
                minutes = rng.randint(*spec.reversal_delay_minutes)
                back_id = next_txn_id("TXN", 7)
                back = t.model_copy(
                    update={
                        "txn_id": back_id,
                        "direction": "credit",
                        "ts": t.ts + timedelta(minutes=minutes),
                        "memo_ref": f"MEMO-{back_id}",
                        "reverses_txn_id": t.txn_id,
                    }
                )
                added.append(TxnRow(back, "reversal: transfer failed"))
                ds.defects.append(
                    DataDefect(
                        account,
                        "reversal",
                        [t.txn_id, back_id],
                        {"amount": str(t.amount), "delay_minutes": str(minutes)},
                    )
                )
    diff.unfed = [r for r in ds.transactions if r.txn.txn_id in drop]
    ds.transactions = [r for r in ds.transactions if r.txn.txn_id not in drop] + added
    return diff
