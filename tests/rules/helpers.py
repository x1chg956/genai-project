"""Builders for rule tests. Timestamps are given in Asia/Kolkata local time."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from itertools import count
from zoneinfo import ZoneInfo

from auditguard.config import REPO_ROOT
from auditguard.schemas import Transaction
from auditguard.schemas.ledger import Channel, Direction
from mcp_servers.aml_engine.engine.registry import RulePack, load_rule_pack

IST = ZoneInfo("Asia/Kolkata")
BASE = datetime(2026, 3, 1, 10, 0, tzinfo=IST)
ACCOUNT = "ACCT-SYN-000001"
_ids = count(1)


def txn(
    amount: str,
    direction: Direction = "credit",
    *,
    channel: Channel = "UPI",
    day: int = 0,
    hours: float = 0,
    at: datetime | None = None,
    cp: str | None = "CPTY-SYN-000001",
    account: str = ACCOUNT,
    txn_id: str | None = None,
    currency: str = "INR",
) -> Transaction:
    ts = at if at is not None else BASE + timedelta(days=day, hours=hours)
    return Transaction(
        txn_id=txn_id or f"TXN-SYN-{next(_ids):07d}",
        account_token=account,
        ts=ts,
        amount=Decimal(amount),
        direction=direction,
        channel=channel,
        counterparty_token=cp,
        currency=currency,
    )


def pack() -> RulePack:
    return load_rule_pack(REPO_ROOT / "rules" / "IN", version="1.0.0")
