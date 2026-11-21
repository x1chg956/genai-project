"""Shared helpers for rule functions. Pure: no I/O, no clock, no randomness."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TypeVar
from zoneinfo import ZoneInfo

from pydantic import Field

from auditguard.schemas import Model, Transaction
from auditguard.schemas.common import Money, Ratio


class LedgerDefectError(ValueError):
    """The ledger handed to a rule is not valid rule input (a data-quality problem)."""


class RuleParams(Model):
    """Base for every rule's parameters. Identity fields are filled from the rule YAML."""

    version: str
    explanation_template_id: str


def validate_ledger(ledger: Sequence[Transaction]) -> None:
    """Reject input that ingest_validate should have cleaned: duplicates, mixed currency, or
    transactions from more than one account."""
    seen: set[str] = set()
    for t in ledger:
        if t.txn_id in seen:
            raise LedgerDefectError(f"duplicate txn_id {t.txn_id}")
        seen.add(t.txn_id)
        if t.currency != "INR":
            raise LedgerDefectError(f"non-INR transaction {t.txn_id} ({t.currency})")
    accounts = {t.account_token for t in ledger}
    if len(accounts) > 1:
        raise LedgerDefectError(f"ledger mixes accounts: {sorted(accounts)}")


def local_date(t: Transaction, tz: ZoneInfo) -> date:
    return t.ts.astimezone(tz).date()


T = TypeVar("T")


@dataclass(frozen=True)
class Window:
    start: date
    end: date  # inclusive
    members: tuple[Transaction, ...]


def best_window(
    items: Sequence[Transaction],
    window_days: int,
    tz: ZoneInfo,
    measure: Callable[[Sequence[Transaction]], int],
) -> Window | None:
    """The window of `window_days` consecutive local calendar days that maximises `measure`.

    A window starting on day d covers d .. d + window_days - 1 (inclusive). Only windows
    starting on a day with a member are considered; ties go to the earliest window.
    """
    if not items:
        return None
    ordered = sorted(items, key=lambda t: (t.ts, t.txn_id))
    best: Window | None = None
    best_value = -1
    starts = sorted({local_date(t, tz) for t in ordered})
    for start in starts:
        end = start + timedelta(days=window_days - 1)
        members = tuple(t for t in ordered if start <= local_date(t, tz) <= end)
        value = measure(members)
        if value > best_value:
            best, best_value = Window(start, end, members), value
    return best


def ratio(numerator: Decimal | int, denominator: Decimal | int) -> Decimal:
    """Exact ratio quantised to 4 places for `score`; 0 when the denominator is 0."""
    if denominator == 0:
        return Decimal("0")
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


__all__ = [
    "Field",
    "LedgerDefectError",
    "Money",
    "Ratio",
    "RuleParams",
    "Window",
    "best_window",
    "local_date",
    "ratio",
    "validate_ledger",
]
