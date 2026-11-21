"""Shared helpers for MCP server tests: tokens, calls, a guard and a small ledger."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastmcp import Client, FastMCP

from auditguard.config import load_settings
from auditguard.schemas import Alert, KycProfile, Transaction
from auditguard.security.tokens import issue_token
from mcp_servers.common.guard import ToolGuard
from mcp_servers.common.ledger import InMemoryLedgerRepo
from tests.conftest import TEST_ENV

KEY = TEST_ENV["MCP_TOKEN_KEY"].encode()
NOW = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
SETTINGS = load_settings(environ=TEST_ENV)

CASE = "ALR-SYN-0001"
ACCT = "ACCT-SYN-000001"
OTHER_CASE = "ALR-SYN-0002"
OTHER_ACCT = "ACCT-SYN-000002"


def token(agent: str, case_id: str = CASE, *, key: bytes = KEY, ttl_min: int = 30) -> str:
    return issue_token(agent, case_id, key=key, ttl=timedelta(minutes=ttl_min), now=NOW)


def token_now(agent: str, case_id: str = CASE) -> str:
    """For subprocess servers, whose guard uses the real clock."""
    return issue_token(agent, case_id, key=KEY, ttl=timedelta(minutes=5), now=datetime.now(UTC))


def guard(name: str) -> ToolGuard:
    return ToolGuard(name, token_key=KEY, allow_list=SETTINGS.mcp.allow_list, clock=lambda: NOW)


@dataclass(frozen=True)
class Called:
    is_error: bool
    result: dict[str, Any]

    @property
    def code(self) -> str | None:
        code = self.result.get("error_code")
        return code if isinstance(code, str) else None


async def acall(client: Client[Any], tool: str, args: dict[str, Any]) -> Called:
    r = await client.call_tool(tool, args, raise_on_error=False)
    sc = r.structured_content
    assert isinstance(sc, dict), f"{tool}: no structured content: {r.content}"
    return Called(is_error=r.is_error, result=sc["result"])


def call(server: FastMCP | Client[Any], tool: str, args: dict[str, Any]) -> Called:
    async def run() -> Called:
        client = server if isinstance(server, Client) else Client(server)
        async with client:
            return await acall(client, tool, args)

    return asyncio.run(run())


def call_many(
    server: FastMCP | Client[Any], calls: list[tuple[str, dict[str, Any]]]
) -> list[Called]:
    """Several calls in one client session (a Client cannot span event loops)."""

    async def run() -> list[Called]:
        client = server if isinstance(server, Client) else Client(server)
        async with client:
            return [await acall(client, tool, args) for tool, args in calls]

    return asyncio.run(run())


def ist(day: int, hour: int, minute: int = 0) -> datetime:
    """March 2026, Asia/Kolkata local time, as UTC."""
    return datetime(2026, 3, day, hour, minute, tzinfo=UTC) - timedelta(hours=5, minutes=30)


def txn(
    n: int,
    when: datetime,
    amount: str,
    direction: str = "credit",
    channel: str = "UPI",
    cpty: str | None = None,
    account: str = ACCT,
) -> Transaction:
    return Transaction.model_validate(
        {
            "txn_id": f"TXN-SYN-{n:07d}",
            "account_token": account,
            "ts": when,
            "amount": Decimal(amount),
            "direction": direction,
            "channel": channel,
            "counterparty_token": cpty,
            "memo_ref": f"MEMO-SYN-{n:07d}",
        }
    )


def small_ledger() -> list[Transaction]:
    """Hand-built so every total in the tests is checked by hand."""
    return [
        txn(1, ist(20, 10), "100.00", "credit", "UPI", "CPTY-SYN-000001"),
        txn(2, ist(25, 11), "250.50", "credit", "UPI", "CPTY-SYN-000002"),
        txn(3, ist(28, 12), "300.25", "credit", "UPI", "CPTY-SYN-000001"),
        txn(4, ist(29, 9), "400.00", "credit", "UPI", "CPTY-SYN-000003"),
        txn(5, ist(30, 15), "900.00", "debit", "NEFT", "CPTY-SYN-000009"),
        txn(6, ist(31, 23, 59), "50.00", "debit", "CASH"),
        txn(7, ist(1, 8), "5.00", "credit", "UPI", "CPTY-SYN-000001"),  # outside 7-day window
        txn(8, ist(29, 10), "999.00", "credit", "UPI", "CPTY-SYN-000005", account=OTHER_ACCT),
    ]


def small_repo() -> InMemoryLedgerRepo:
    return InMemoryLedgerRepo(
        alerts=[
            Alert(
                alert_id=CASE, account_ref=ACCT, scenario_code="RPT", detected_at=ist(31, 23, 59)
            ),
            Alert(
                alert_id=OTHER_CASE,
                account_ref=OTHER_ACCT,
                scenario_code="FAN",
                detected_at=ist(31, 12),
            ),
        ],
        transactions=small_ledger(),
        kyc_by_account={
            ACCT: KycProfile(
                subject_token="CUST-SYN-000001",
                segment="retail",
                kyc_risk_band="low",
                declared_monthly_turnover=Decimal("50000.00"),
                onboarded_at=datetime(2024, 1, 1, tzinfo=UTC),
            )
        },
    )
