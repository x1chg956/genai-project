"""transactions server: get_transactions, get_account_summary, get_counterparty_flows,
get_kyc_profile, get_linked_accounts, get_peer_baseline, get_prior_reports.

Every tool is R (read). `agent_token` and `case_id` are checked by the ToolGuard before the
tool runs; the tool then checks that the account or subject is in the case's scope (the
alerted account, the bank's accounts linked to it, and their holders). Memo text never
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
from auditguard.schemas import Alert, KycProfile, ToolError, Transaction
from mcp_servers.common.errors import tool_error
from mcp_servers.common.guard import ToolGuard, guard_from_settings
from mcp_servers.common.labels import READ_ONLY
from mcp_servers.common.ledger import (
    LedgerRepo,
    PostgresLedgerRepo,
    case_accounts,
    case_subjects,
    local_day_bounds,
)
from mcp_servers.common.quality import assess, rule_input
from mcp_servers.transactions import stats
from mcp_servers.transactions.models import (
    AccountSummary,
    CounterpartyFlows,
    LedgerPage,
    LinkedAccounts,
    PeerBaseline,
    PriorReports,
)

WindowDays = Annotated[int, Field(ge=1, le=366)]
MAX_RANGE_DAYS = 366


def build_server(
    repo: LedgerRepo,
    guard: ToolGuard,
    *,
    tz: ZoneInfo,
    max_rows: int,
    peer_min_accounts: int = 10,
) -> FastMCP:
    mcp = FastMCP("transactions", middleware=[guard])

    def subject(tool: str, case_id: str, subject_token: str) -> tuple[str, list[str]] | ToolError:
        """(customer token, the case's accounts it holds) for a customer or account token."""
        alert = repo.alert(case_id)
        if alert is None:
            return tool_error("NOT_FOUND", f"no case {case_id}", tool)
        accounts = case_accounts(repo, alert)
        if subject_token in accounts:
            kyc = repo.kyc_for_account(subject_token)
            if kyc is None:
                return tool_error("NOT_FOUND", "no customer holds this account", tool)
            return kyc.subject_token, [subject_token]
        if subject_token in case_subjects(repo, alert):
            held = sorted(set(repo.accounts_of(subject_token)) & accounts)
            return subject_token, held
        return tool_error("OUT_OF_SCOPE", "subject is not in this case's scope", tool)

    def scoped(tool: str, case_id: str, account_token: str) -> Alert | ToolError:
        """The case's alert if the account is in the case's scope, else a ToolError."""
        alert = repo.alert(case_id)
        if alert is None:
            return tool_error("NOT_FOUND", f"no case {case_id}", tool)
        if account_token not in case_accounts(repo, alert):
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
        `ledger_ref` names the whole range for evaluate_rules. `quality` covers the whole
        range: days whose closing balance does not reconcile with the rows (missing or extra
        rows), duplicate postings quarantined and reversal pairs netted. Every row on file is
        listed, including quarantined and netted ones."""
        tool = "get_transactions"
        alert = scoped(tool, case_id, account_token)
        if isinstance(alert, ToolError):
            return alert
        if from_date > to_date:
            return tool_error("INVALID_INPUT", "from_date is after to_date", tool)
        if (to_date - from_date).days + 1 > MAX_RANGE_DAYS:
            return tool_error("INVALID_INPUT", f"range is longer than {MAX_RANGE_DAYS} days", tool)
        rows = repo.transactions(account_token, *local_day_bounds(from_date, to_date, tz))
        opened = repo.account_opened(account_token)
        quality = assess(
            rows,
            repo.closing_balances(account_token, from_date, to_date),
            from_date,
            to_date,
            tz,
            opened.astimezone(tz).date() if opened else None,
        )
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
            quality=quality,
            transactions=rows[start : start + max_rows],
        )

    @mcp.tool(tags={"read"}, annotations=READ_ONLY)
    def get_account_summary(
        agent_token: str, case_id: str, account_token: str, window_days: WindowDays
    ) -> AccountSummary | ToolError:
        """R. Counts, totals, p50 and p90 per channel and direction over the last
        window_days local days up to the alert date. Exact decimal amounts. Duplicate
        postings and netted reversal pairs are left out, as in evaluate_rules."""
        tool = "get_account_summary"
        alert = scoped(tool, case_id, account_token)
        if isinstance(alert, ToolError):
            return alert
        start, end = window(alert, window_days)
        rows = rule_input(repo.transactions(account_token, *local_day_bounds(start, end, tz)))
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
        up to the alert date, with credit and debit counts and totals. Duplicate postings
        and netted reversal pairs are left out, as in evaluate_rules."""
        tool = "get_counterparty_flows"
        alert = scoped(tool, case_id, account_token)
        if isinstance(alert, ToolError):
            return alert
        start, end = window(alert, window_days)
        rows = rule_input(repo.transactions(account_token, *local_day_bounds(start, end, tz)))
        flows = stats.counterparty_flows(rows)
        return CounterpartyFlows(
            account_token=account_token,
            window_days=window_days,
            from_date=start,
            to_date=end,
            counterparties_total=len(flows),
            flows=flows[:top_k],
        )

    @mcp.tool(tags={"read"}, annotations=READ_ONLY)
    def get_kyc_profile(
        agent_token: str, case_id: str, subject_token: str
    ) -> KycProfile | ToolError:
        """R. The KYC profile of a customer in the case's scope: segment, risk band,
        declared monthly turnover and onboarding time. `subject_token` is a customer token
        or one of the case's account tokens (its holder is returned)."""
        tool = "get_kyc_profile"
        found = subject(tool, case_id, subject_token)
        if isinstance(found, ToolError):
            return found
        kyc = repo.kyc_for_customer(found[0])
        return kyc or tool_error("NOT_FOUND", "no KYC profile on file", tool)

    @mcp.tool(tags={"read"}, annotations=READ_ONLY)
    def get_linked_accounts(
        agent_token: str, case_id: str, subject_token: str
    ) -> LinkedAccounts | ToolError:
        """R. Accounts linked to the case's accounts by holder, introducer, phone or device.
        `subject_token` is a customer or account token in the case's scope. A linked
        account of this bank (holder_token set) is in the case's scope."""
        tool = "get_linked_accounts"
        found = subject(tool, case_id, subject_token)
        if isinstance(found, ToolError):
            return found
        customer, accounts = found
        links = [link for a in accounts for link in repo.links(a)]
        return LinkedAccounts(subject_token=customer, links=links[:max_rows])

    @mcp.tool(tags={"read"}, annotations=READ_ONLY)
    def get_prior_reports(
        agent_token: str, case_id: str, subject_token: str
    ) -> PriorReports | ToolError:
        """R. Reports (STR, CTR) filed earlier on a customer in the case's scope, oldest
        first. `subject_token` is a customer or account token in the case's scope."""
        tool = "get_prior_reports"
        found = subject(tool, case_id, subject_token)
        if isinstance(found, ToolError):
            return found
        reports = repo.prior_reports(found[0])
        return PriorReports(subject_token=found[0], reports=reports[:max_rows])

    @mcp.tool(tags={"read"}, annotations=READ_ONLY)
    def get_peer_baseline(
        agent_token: str, case_id: str, segment: str, window_days: WindowDays
    ) -> PeerBaseline | ToolError:
        """R. What is usual for a customer segment: p50 and p90 across peer accounts of each
        account's credit, debit and cash totals and transaction count over the last
        window_days local days up to the alert date. Peers are the segment's accounts
        open for the whole window, excluding this case's accounts; duplicates and netted
        reversals are left out. Aggregates only, never a peer's identity."""
        tool = "get_peer_baseline"
        alert = repo.alert(case_id)
        if alert is None:
            return tool_error("NOT_FOUND", f"no case {case_id}", tool)
        start, end = window(alert, window_days)
        bounds = local_day_bounds(start, end, tz)
        excluded = case_accounts(repo, alert)
        peers = [a for a in repo.peer_accounts(segment, bounds[0]) if a not in excluded]
        if len(peers) < peer_min_accounts:
            return tool_error(
                "INVALID_INPUT",
                f"segment has {len(peers)} peer accounts; at least {peer_min_accounts} needed",
                tool,
            )
        by_account: dict[str, list[Transaction]] = {a: [] for a in peers}
        for t in repo.transactions_for(peers, *bounds):
            by_account[t.account_token].append(t)
        totals = [stats.account_totals(rule_input(rows)) for rows in by_account.values()]
        return PeerBaseline(
            segment=segment,
            window_days=window_days,
            from_date=start,
            to_date=end,
            peer_accounts=len(peers),
            credit_total=stats.peer_stat([t.credit for t in totals]),
            debit_total=stats.peer_stat([t.debit for t in totals]),
            cash_total=stats.peer_stat([t.cash for t in totals]),
            txn_count=stats.peer_count([t.count for t in totals]),
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
        peer_min_accounts=settings.mcp.peer_min_accounts,
    )
