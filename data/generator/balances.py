"""End-of-day closing balances, from what the bank booked (not from the possibly defective feed).

`ingest_validate` reconciles these against the transaction feed to find missing days
(mcp_servers/common/quality.py). Balances start on the later of the account's opening day
and `lookback_days` before the dataset start, and run to `last_day`; quiet days carry the
balance forward.

Opening balance: zero for an account opened inside that range (a new account), otherwise a
carried-in balance high enough that the account never goes below INR 10,000.00 in the
range. A new account can go below zero (an overdraft); no agent tool returns balances.
Deterministic: no random draws, so seeded datasets are unchanged.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from data.generator.model import AccountRow, BalanceRow, TxnRow

IST = ZoneInfo("Asia/Kolkata")
FLOOR = Decimal("10000.00")
ZERO = Decimal("0.00")
LOOKBACK_DAYS = 366  # the longest ingest window config allows


def daily_balances(
    booked: Iterable[TxnRow],
    accounts: Iterable[AccountRow],
    first_day: date,
    last_day: date,
) -> list[BalanceRow]:
    net: dict[str, dict[date, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for row in booked:
        t = row.txn
        net[t.account_token][t.ts.astimezone(IST).date()] += (
            t.amount if t.direction == "credit" else -t.amount
        )
    out: list[BalanceRow] = []
    for account in accounts:
        opened = account.opened_at.astimezone(IST).date()
        start = max(opened, first_day)
        days = net[account.account_token]
        running, lowest = ZERO, ZERO
        day = start
        while day <= last_day:
            running += days[day]
            lowest = min(lowest, running)
            day += timedelta(days=1)
        balance = ZERO if opened >= first_day else FLOOR - lowest
        if opened < first_day:  # the carried-in balance, so the first day reconciles too
            out.append(BalanceRow(account.account_token, start - timedelta(days=1), balance))
        day = start
        while day <= last_day:
            balance += days[day]
            out.append(BalanceRow(account.account_token, day, balance))
            day += timedelta(days=1)
    return out
