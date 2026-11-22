"""Ledger references: a storage key for a slice of one account's ledger (docs/03 §3).

`ledger:v1:<account>:<from>:<to>:<digest>` names the rows of one account between two
Asia/Kolkata local dates (inclusive). The digest is over the rows themselves, so a consumer
(aml_engine) can tell when the ledger changed after ingest and refuse to evaluate it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from auditguard.schemas import Transaction

_PREFIX = "ledger:v1"


class LedgerRefError(ValueError):
    """The string is not a valid ledger reference."""


@dataclass(frozen=True)
class LedgerRef:
    account_token: str
    from_date: date
    to_date: date
    digest: str

    def __str__(self) -> str:
        return (
            f"{_PREFIX}:{self.account_token}:{self.from_date.isoformat()}:"
            f"{self.to_date.isoformat()}:{self.digest}"
        )


def ledger_digest(rows: Iterable[Transaction]) -> str:
    """First 16 hex chars of SHA-256 over the rows, sorted by txn_id."""
    h = hashlib.sha256()
    for t in sorted(rows, key=lambda r: r.txn_id):
        line = "|".join(
            [
                t.txn_id,
                t.account_token,
                t.ts.isoformat(),
                str(t.amount),
                t.currency,
                t.direction,
                t.channel,
                t.counterparty_token or "",
            ]
        )
        if t.reverses_txn_id:  # appended only when set, so older digests stay stable
            line += f"|rev={t.reverses_txn_id}"
        h.update(line.encode("utf-8") + b"\n")
    return h.hexdigest()[:16]


def make_ledger_ref(
    account_token: str, from_date: date, to_date: date, rows: Iterable[Transaction]
) -> LedgerRef:
    return LedgerRef(account_token, from_date, to_date, ledger_digest(rows))


def parse_ledger_ref(text: str) -> LedgerRef:
    parts = text.split(":")
    if len(parts) != 6 or ":".join(parts[:2]) != _PREFIX:
        raise LedgerRefError("expected ledger:v1:<account>:<from>:<to>:<digest>")
    account, start, end, digest = parts[2:]
    try:
        from_date, to_date = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise LedgerRefError("ledger ref dates must be YYYY-MM-DD") from None
    if from_date > to_date:
        raise LedgerRefError("ledger ref from_date is after to_date")
    if len(digest) != 16 or any(c not in "0123456789abcdef" for c in digest):
        raise LedgerRefError("ledger ref digest must be 16 lowercase hex characters")
    return LedgerRef(account, from_date, to_date, digest)
