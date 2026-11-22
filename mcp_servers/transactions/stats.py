"""Exact aggregates over transactions. Pure Decimal arithmetic, no floats."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar

from auditguard.schemas import Transaction
from mcp_servers.transactions.models import (
    ChannelStats,
    CounterpartyFlow,
    PeerCount,
    PeerStat,
)

ZERO = Decimal("0.00")


N = TypeVar("N", int, Decimal)


def nearest_rank(sorted_values: Sequence[N], pct: int) -> N:
    """Nearest-rank percentile: the smallest value with at least pct% of values at or below it.

    Always an observed amount, so it is exact to the paisa.
    """
    if not sorted_values:
        raise ValueError("percentile of an empty sequence")
    n = len(sorted_values)
    rank = -(-pct * n // 100)  # ceil(pct * n / 100) in integers
    return sorted_values[max(rank, 1) - 1]


def channel_stats(txns: Sequence[Transaction]) -> list[ChannelStats]:
    groups: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    for t in txns:
        groups[(t.channel, t.direction)].append(t.amount)
    out = []
    for (channel, direction), amounts in sorted(groups.items()):
        amounts.sort()
        out.append(
            ChannelStats.model_validate(
                {
                    "channel": channel,
                    "direction": direction,
                    "count": len(amounts),
                    "total": sum(amounts, ZERO),
                    "p50": nearest_rank(amounts, 50),
                    "p90": nearest_rank(amounts, 90),
                }
            )
        )
    return out


def counterparty_flows(txns: Sequence[Transaction]) -> list[CounterpartyFlow]:
    """One flow per counterparty, largest total value first (ties by token)."""
    by_cp: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        if t.counterparty_token:
            by_cp[t.counterparty_token].append(t)
    flows = []
    for token, items in by_cp.items():
        credits = [t.amount for t in items if t.direction == "credit"]
        debits = [t.amount for t in items if t.direction == "debit"]
        flows.append(
            CounterpartyFlow(
                counterparty_token=token,
                credit_count=len(credits),
                credit_total=sum(credits, ZERO),
                debit_count=len(debits),
                debit_total=sum(debits, ZERO),
                first_seen=min(t.ts for t in items),
                last_seen=max(t.ts for t in items),
            )
        )
    flows.sort(key=lambda f: (-(f.credit_total + f.debit_total), f.counterparty_token))
    return flows


@dataclass(frozen=True)
class AccountTotals:
    credit: Decimal
    debit: Decimal
    cash: Decimal  # CASH channel, both directions
    count: int


def account_totals(txns: Sequence[Transaction]) -> AccountTotals:
    return AccountTotals(
        credit=sum((t.amount for t in txns if t.direction == "credit"), ZERO),
        debit=sum((t.amount for t in txns if t.direction == "debit"), ZERO),
        cash=sum((t.amount for t in txns if t.channel == "CASH"), ZERO),
        count=len(txns),
    )


def peer_stat(values: Sequence[Decimal]) -> PeerStat:
    ordered = sorted(values)
    return PeerStat(p50=nearest_rank(ordered, 50), p90=nearest_rank(ordered, 90))


def peer_count(values: Sequence[int]) -> PeerCount:
    ordered = sorted(values)
    return PeerCount(p50=nearest_rank(ordered, 50), p90=nearest_rank(ordered, 90))
