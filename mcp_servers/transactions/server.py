"""transactions server: get_transactions, get_account_summary, get_counterparty_flows.

Every tool is R (read). `agent_token` and `case_id` are checked by the ToolGuard before the
tool runs; the tool then checks that the account is in the case's scope. Memo text never
leaves this server (rows carry memo_ref only).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastmcp import FastMCP
from pydantic import Field

from auditguard.config import Settings
from auditguard.db import conninfo
from auditguard.ledger_ref import make_ledger_ref
from auditguard.schemas import Alert, ToolError
from mcp_servers.common.errors import tool_error
from mcp_servers.common.guard import ToolGuard, guard_from_settings
from mcp_servers.common.labels import READ_ONLY
from mcp_servers.common.ledger import (
    LedgerRepo,
    PostgresLedgerRepo,
    case_accounts,
    local_day_bounds,
)
from mcp_servers.transactions import stats
from mcp_servers.transactions.models import AccountSummary, CounterpartyFlows, LedgerPage

WindowDays = Annotated[int, Field(ge=1, le=366)]
MAX_RANGE_DAYS = 366


def build_server(repo: LedgerRepo, guard: ToolGuard, *, tz: ZoneInfo, max_rows: int) -> FastMCP:
    mcp = FastMCP("transactions", middleware=[guard])

    def scoped(tool: str, case_id: str, account_token: str) -> Alert | ToolError:
        """The case's alert if the account is in the case's scope, else a ToolError."""
        alert = repo.alert(case_id)
        if alert is None:
            return tool_error("NOT_FOUND", f"no case {case_id}", tool)
        if account_token not in case_accounts(alert):
            return tool_error("OUT_OF_SCOPE", "account is not in this case's scope", tool)
        return alert

    def window(alert: Alert, window_days: int) -> tuple[date, date]:
        """window_days local days ending on the alert's detection date (inclusive)."""
        end = alert.detected_at.astimezone(tz).date()
        return end - timedelta(days=window_days - 1), end

    @mcp.tool(tags={"read"}, annotations=READ_ONLY)
    def get_transactions(
        agent_token: str,
        case_id: str,
        account_token: str,
        from_date: date,
        to_date: date,
        page: Annotated[int, Field(ge=1)] = 1,
    ) -> LedgerPage | ToolError:
        """R. One page (at most 200 rows) of an account's transactions between two
        Asia/Kolkata local dates, inclusive, ordered by time. Amounts are decimal strings.
        `ledger_ref` names the whole range for evaluate_rules."""
        tool = "get_transactions"
        alert = scoped(tool, case_id, account_token)
        if isinstance(alert, ToolError):
            return alert
        if from_date > to_date:
            return tool_error("INVALID_INPUT", "from_date is after to_date", tool)
        if (to_date - from_date).days + 1 > MAX_RANGE_DAYS:
            return tool_error("INVALID_INPUT", f"range is longer than {MAX_RANGE_DAYS} days", tool)
        rows = repo.transactions(account_token, *local_day_bounds(from_date, to_date, tz))
        start = (page - 1) * max_rows
        return LedgerPage(
            account_token=account_token,
            from_date=from_date,
            to_date=to_date,
            page=page,
            page_size=max_rows,
            total=len(rows),
            has_more=start + max_rows < len(rows),
            ledger_ref=str(make_ledger_ref(account_token, from_date, to_date, rows)),
            transactions=rows[start : start + max_rows],
        )

    @mcp.tool(tags={"read"}, annotations=READ_ONLY)
    def get_account_summary(
        agent_token: str, case_id: str, account_token: str, window_days: WindowDays
    ) -> AccountSummary | ToolError:
        """R. Counts, totals, p50 and p90 per channel and direction over the last
        window_days local days up to the alert date. Exact decimal amounts."""
        tool = "get_account_summary"
        alert = scoped(tool, case_id, account_token)
        if isinstance(alert, ToolError):
            return alert
        start, end = window(alert, window_days)
        rows = repo.transactions(account_token, *local_day_bounds(start, end, tz))
        return AccountSummary(
            account_token=account_token,
            window_days=window_days,
            from_date=start,
            to_date=end,
            txn_count=len(rows),
            credit_total=sum((t.amount for t in rows if t.direction == "credit"), stats.ZERO),
            debit_total=sum((t.amount for t in rows if t.direction == "debit"), stats.ZERO),
            by_channel=stats.channel_stats(rows),
        )

    @mcp.tool(tags={"read"}, annotations=READ_ONLY)
    def get_counterparty_flows(
        agent_token: str,
        case_id: str,
        account_token: str,
        window_days: WindowDays,
        top_k: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> CounterpartyFlows | ToolError:
        """R. The top_k counterparties by total value over the last window_days local days
        up to the alert date, with credit and debit counts and totals."""
        tool = "get_counterparty_flows"
        alert = scoped(tool, case_id, account_token)
        if isinstance(alert, ToolError):
            return alert
        start, end = window(alert, window_days)
        rows = repo.transactions(account_token, *local_day_bounds(start, end, tz))
        flows = stats.counterparty_flows(rows)
        return CounterpartyFlows(
            account_token=account_token,
            window_days=window_days,
            from_date=start,
            to_date=end,
            counterparties_total=len(flows),
            flows=flows[:top_k],
        )

    return mcp


def build_from_settings(settings: Settings) -> FastMCP:
    repo = PostgresLedgerRepo(
        conninfo(settings.databases.ledger_ro, application_name="mcp-transactions")
    )
    return build_server(
        repo,
        guard_from_settings("transactions", settings),
        tz=ZoneInfo(settings.timezone),
        max_rows=settings.mcp.max_rows,
    )
