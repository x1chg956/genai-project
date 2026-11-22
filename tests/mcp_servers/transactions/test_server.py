"""transactions server. The ledger is hand-built (helpers.small_ledger) so every total is
checked against arithmetic done by hand, with exact Decimal strings."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastmcp import FastMCP

from auditguard.ledger_ref import parse_ledger_ref
from mcp_servers.common.ledger import InMemoryLedgerRepo, booked_balances
from mcp_servers.transactions.server import build_server
from mcp_servers.transactions.stats import nearest_rank
from tests.mcp_servers.helpers import (
    ACCT,
    CASE,
    OTHER_ACCT,
    OTHER_CASE,
    Called,
    call,
    guard,
    ist,
    small_ledger,
    small_repo,
    token,
    txn,
)

IST = ZoneInfo("Asia/Kolkata")


def server(max_rows: int = 200) -> FastMCP:
    return build_server(small_repo(), guard("transactions"), tz=IST, max_rows=max_rows)


def run(tool: str, max_rows: int = 200, agent: str = "single_agent", **args: Any) -> Called:
    base = {
        "agent_token": token(agent, args.get("case_id", CASE)),
        "case_id": CASE,
        "account_token": ACCT,
    }
    return call(server(max_rows), tool, base | args)


# get_transactions


@pytest.mark.tool("get_transactions")
def test_get_transactions_happy() -> None:
    r = run("get_transactions", from_date="2026-03-01", to_date="2026-03-31")
    assert not r.is_error
    ids = [t["txn_id"] for t in r.result["transactions"]]
    assert ids == [f"TXN-SYN-{n:07d}" for n in (7, 1, 2, 3, 4, 5, 6)]
    assert r.result["total"] == 7 and r.result["has_more"] is False
    assert r.result["transactions"][2]["amount"] == "250.50"
    ref = parse_ledger_ref(r.result["ledger_ref"])
    assert (ref.account_token, str(ref.from_date), str(ref.to_date)) == (
        ACCT,
        "2026-03-01",
        "2026-03-31",
    )


@pytest.mark.tool("get_transactions")
def test_get_transactions_local_day_boundary() -> None:
    # TXN 6 is at 23:59 IST on 31 March: in a range ending on the 31st, not on the 30th.
    r = run("get_transactions", from_date="2026-03-30", to_date="2026-03-30")
    assert [t["txn_id"] for t in r.result["transactions"]] == ["TXN-SYN-0000005"]
    r = run("get_transactions", from_date="2026-03-31", to_date="2026-03-31")
    assert [t["txn_id"] for t in r.result["transactions"]] == ["TXN-SYN-0000006"]


@pytest.mark.tool("get_transactions")
def test_get_transactions_pages_never_overlap() -> None:
    pages = [
        run("get_transactions", max_rows=3, from_date="2026-03-01", to_date="2026-03-31", page=p)
        for p in (1, 2, 3, 4)
    ]
    sizes = [len(p.result["transactions"]) for p in pages]
    assert sizes == [3, 3, 1, 0]
    assert [p.result["has_more"] for p in pages] == [True, True, False, False]
    ids = [t["txn_id"] for p in pages for t in p.result["transactions"]]
    assert len(ids) == len(set(ids)) == 7
    assert len({p.result["ledger_ref"] for p in pages}) == 1  # ref names the range, not the page


@pytest.mark.tool("get_transactions")
def test_get_transactions_never_returns_memo_text() -> None:
    r = run("get_transactions", from_date="2026-03-01", to_date="2026-03-31")
    assert all("memo" not in t for t in r.result["transactions"])
    assert all(t["memo_ref"].startswith("MEMO-SYN-") for t in r.result["transactions"])


@pytest.mark.tool("get_transactions")
@pytest.mark.parametrize(
    "args",
    [
        {"from_date": "2026-03-31", "to_date": "2026-03-01"},
        {"from_date": "2025-01-01", "to_date": "2026-03-31"},
        {"from_date": "31/03/2026", "to_date": "2026-03-31"},
        {"from_date": "2026-03-01", "to_date": "2026-03-31", "page": 0},
        {"from_date": "2026-03-01"},
    ],
)
def test_get_transactions_invalid_input(args: dict[str, Any]) -> None:
    r = run("get_transactions", **args)
    assert r.is_error and r.code == "INVALID_INPUT"


# get_account_summary


@pytest.mark.tool("get_account_summary")
def test_get_account_summary_happy() -> None:
    r = run("get_account_summary", window_days=7)
    assert not r.is_error
    s = r.result
    assert (s["from_date"], s["to_date"], s["txn_count"]) == ("2026-03-25", "2026-03-31", 5)
    assert s["credit_total"] == "950.75"  # 250.50 + 300.25 + 400.00
    assert s["debit_total"] == "950.00"  # 900.00 + 50.00
    assert s["by_channel"] == [
        {
            "channel": "CASH",
            "direction": "debit",
            "count": 1,
            "total": "50.00",
            "p50": "50.00",
            "p90": "50.00",
        },
        {
            "channel": "NEFT",
            "direction": "debit",
            "count": 1,
            "total": "900.00",
            "p50": "900.00",
            "p90": "900.00",
        },
        {
            "channel": "UPI",
            "direction": "credit",
            "count": 3,
            "total": "950.75",
            "p50": "300.25",
            "p90": "400.00",
        },
    ]


@pytest.mark.tool("get_account_summary")
@pytest.mark.parametrize("window_days", [0, 367, "seven"])
def test_get_account_summary_invalid_input(window_days: object) -> None:
    r = run("get_account_summary", window_days=window_days)
    assert r.is_error and r.code == "INVALID_INPUT"


# get_counterparty_flows


@pytest.mark.tool("get_counterparty_flows")
def test_get_counterparty_flows_happy() -> None:
    r = run("get_counterparty_flows", window_days=7, top_k=2)
    assert not r.is_error
    assert r.result["counterparties_total"] == 4
    flows = r.result["flows"]
    assert [f["counterparty_token"] for f in flows] == ["CPTY-SYN-000009", "CPTY-SYN-000003"]
    assert (flows[0]["debit_count"], flows[0]["debit_total"], flows[0]["credit_total"]) == (
        1,
        "900.00",
        "0.00",
    )


@pytest.mark.tool("get_counterparty_flows")
def test_get_counterparty_flows_window_boundary() -> None:
    # TXN 7 (1 March) is outside a 30-day window ending 31 March and inside a 31-day one.
    def cpty1(days: int) -> dict[str, Any]:
        flows = run("get_counterparty_flows", window_days=days, top_k=50).result["flows"]
        return next(f for f in flows if f["counterparty_token"] == "CPTY-SYN-000001")

    assert (cpty1(30)["credit_count"], cpty1(30)["credit_total"]) == (2, "400.25")
    assert (cpty1(31)["credit_count"], cpty1(31)["credit_total"]) == (3, "405.25")


@pytest.mark.tool("get_counterparty_flows")
@pytest.mark.parametrize(
    "args", [{"window_days": 7, "top_k": 0}, {"window_days": 7, "top_k": 51}, {"top_k": 5}]
)
def test_get_counterparty_flows_invalid_input(args: dict[str, Any]) -> None:
    r = run("get_counterparty_flows", **args)
    assert r.is_error and r.code == "INVALID_INPUT"


# identity and scope


@pytest.mark.parametrize(
    ("tool", "extra"),
    [
        ("get_transactions", {"from_date": "2026-03-01", "to_date": "2026-03-31"}),
        ("get_account_summary", {"window_days": 7}),
        ("get_counterparty_flows", {"window_days": 7}),
    ],
)
def test_wrong_agent_token(tool: str, extra: dict[str, Any]) -> None:
    for agent in ("researcher", "build_payload", "submit"):
        r = run(tool, agent=agent, **extra)
        assert r.code == "FORBIDDEN", (tool, agent)
    forged = call(
        server(),
        tool,
        {"agent_token": token("single_agent", key=b"x"), "case_id": CASE, "account_token": ACCT}
        | extra,
    )
    assert forged.code == "FORBIDDEN"


@pytest.mark.tool("get_account_summary")
def test_out_of_scope_case() -> None:
    # A token for case 1 cannot read case 2, and case 1 cannot read case 2's account.
    r = call(
        server(),
        "get_account_summary",
        {
            "agent_token": token("single_agent", CASE),
            "case_id": OTHER_CASE,
            "account_token": OTHER_ACCT,
            "window_days": 7,
        },
    )
    assert r.code == "OUT_OF_SCOPE"
    r = run("get_account_summary", account_token=OTHER_ACCT, window_days=7)
    assert r.code == "OUT_OF_SCOPE"


@pytest.mark.tool("get_account_summary")
def test_unknown_case_not_found() -> None:
    r = run("get_account_summary", case_id="ALR-SYN-9999", window_days=7)
    assert r.code == "NOT_FOUND"


# exact percentile


def test_nearest_rank_exact() -> None:
    v = [Decimal(x) for x in ("1.00", "2.00", "3.00", "4.00")]
    assert (nearest_rank(v, 50), nearest_rank(v, 90), nearest_rank(v, 100)) == (
        Decimal("2.00"),
        Decimal("4.00"),
        Decimal("4.00"),
    )
    assert nearest_rank(v[:1], 50) == Decimal("1.00")
    with pytest.raises(ValueError, match="empty"):
        nearest_rank([], 50)


# data quality (mcp_servers/common/quality.py) as the tools report it


def defective_server() -> FastMCP:
    """small_ledger plus a double post of txn 2 and a reversed IMPS debit, with books that
    are missing 25 March: the bank booked txn 2 (250.50) but the feed lost it."""
    base = small_ledger()
    rows = [t for t in base if t.account_token == ACCT and t.txn_id != "TXN-SYN-0000002"]
    copy = base[0].model_copy(update={"txn_id": "TXN-SYN-0000020"})  # double post of txn 1
    debit = txn(21, ist(30, 16), "700.00", "debit", "IMPS", "CPTY-SYN-000010")
    back = txn(22, ist(30, 17), "700.00", "credit", "IMPS", "CPTY-SYN-000010").model_copy(
        update={"reverses_txn_id": debit.txn_id}
    )
    books = booked_balances([*base, debit, back], IST)[ACCT]
    repo = InMemoryLedgerRepo(
        alerts=[a for a in (small_repo().alert(CASE),) if a],
        transactions=[*rows, copy, debit, back],
        balances={ACCT: books},
        opened={ACCT: ist(1, 0)},
    )
    return build_server(repo, guard("transactions"), tz=IST, max_rows=200)


def run_on(srv: FastMCP, tool: str, **args: Any) -> Called:
    base = {"agent_token": token("single_agent"), "case_id": CASE, "account_token": ACCT}
    return call(srv, tool, base | args)


@pytest.mark.tool("get_transactions")
def test_get_transactions_reports_quality_of_a_clean_ledger() -> None:
    q = run("get_transactions", from_date="2026-03-01", to_date="2026-03-31").result["quality"]
    assert (q["days_expected"], q["days_reconciled"], q["unreconciled_days"]) == (31, 31, [])
    assert q["quarantined"] == q["netted"] == q["unmatched_reversals"] == []
    assert q["rule_input_count"] == 7


@pytest.mark.tool("get_transactions")
def test_get_transactions_reports_gap_duplicate_and_reversal() -> None:
    r = run_on(defective_server(), "get_transactions", from_date="2026-03-01", to_date="2026-03-31")
    q = r.result["quality"]
    # balances: books held txn 2, the feed does not, so the day reconciles nowhere else
    assert q["unreconciled_days"] == ["2026-03-25"]
    assert (q["days_expected"], q["days_reconciled"]) == (31, 30)
    assert q["quarantined"] == [{"txn_id": "TXN-SYN-0000020", "duplicate_of": "TXN-SYN-0000001"}]
    assert q["netted"] == [
        {"txn_id": "TXN-SYN-0000021", "reversal_txn_id": "TXN-SYN-0000022", "amount": "700.00"}
    ]
    assert r.result["total"] == 9  # every row on file is listed
    assert q["rule_input_count"] == 6


@pytest.mark.tool("get_account_summary")
def test_get_account_summary_leaves_out_quarantined_and_netted_rows() -> None:
    r = run_on(defective_server(), "get_account_summary", window_days=31)
    # txns 1, 3, 4, 7 credits (100.00 + 300.25 + 400.00 + 5.00); 5, 6 debits (900.00 + 50.00)
    assert (r.result["txn_count"], r.result["credit_total"], r.result["debit_total"]) == (
        6,
        "805.25",
        "950.00",
    )
