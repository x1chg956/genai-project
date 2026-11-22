"""transactions server, Phase 2 tools: get_kyc_profile, get_linked_accounts, get_prior_reports,
get_peer_baseline, and the case scope widened to linked accounts (docs/05).

The ledger is hand-built so every percentile is checked against arithmetic done by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastmcp import FastMCP

from auditguard.schemas import KycProfile, LinkedAccount, PriorReport
from mcp_servers.common.ledger import InMemoryLedgerRepo
from mcp_servers.transactions.server import build_server
from tests.mcp_servers.helpers import (
    ACCT,
    CASE,
    OTHER_ACCT,
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
LINKED = "ACCT-SYN-000003"
EXTERNAL = "ACCT-EXT-000001"
OLD = datetime(2025, 1, 1, tzinfo=UTC)


def kyc(customer: str, segment: str = "retail") -> KycProfile:
    return KycProfile(
        subject_token=customer,
        segment=segment,
        kyc_risk_band="low",
        declared_monthly_turnover=Decimal("50000.00"),
        onboarded_at=OLD,
    )


def peer_rows() -> list[Any]:
    """Peer i (1..10): one UPI credit of i x 100.00; even i also a CASH debit of i x 10.00."""
    rows = []
    for i in range(1, 11):
        acct = f"ACCT-SYN-1000{i:02d}"
        rows.append(txn(100 + i, ist(10, 12), f"{i * 100}.00", "credit", "UPI", account=acct))
        if i % 2 == 0:
            rows.append(txn(200 + i, ist(11, 12), f"{i * 10}.00", "debit", "CASH", account=acct))
    # a late joiner (opened mid-window) is not a peer, however large its totals
    rows.append(txn(300, ist(20, 12), "99999.00", "credit", "UPI", account="ACCT-SYN-100099"))
    return rows


def repo() -> InMemoryLedgerRepo:
    base = small_repo()
    peers = {
        f"ACCT-SYN-1000{i:02d}": kyc(f"CUST-SYN-1000{i:02d}", "salaried") for i in range(1, 11)
    }
    return InMemoryLedgerRepo(
        alerts=[a for a in (base.alert(CASE),) if a],
        transactions=[
            *small_ledger(),
            txn(40, ist(15, 9), "75.00", "debit", "UPI", account=LINKED),
            *peer_rows(),
        ],
        kyc_by_account={
            ACCT: kyc("CUST-SYN-000001"),
            OTHER_ACCT: kyc("CUST-SYN-000002"),
            LINKED: kyc("CUST-SYN-000003", "salaried"),
            **peers,
            "ACCT-SYN-100099": kyc("CUST-SYN-100099", "salaried"),
        },
        opened={"ACCT-SYN-100099": ist(5, 10)},
        links=[
            LinkedAccount(account_token=ACCT, linked_account_token=LINKED, link_type="phone"),
            LinkedAccount(account_token=EXTERNAL, linked_account_token=ACCT, link_type="device"),
            LinkedAccount(
                account_token=OTHER_ACCT, linked_account_token="ACCT-SYN-000009", link_type="holder"
            ),
        ],
        prior_reports=[
            PriorReport(
                report_id="RPT-SYN-0002",
                subject_token="CUST-SYN-000001",
                filed_at=datetime(2025, 9, 1, tzinfo=UTC),
                type="CTR",
                amount=Decimal("1200000.00"),
            ),
            PriorReport(
                report_id="RPT-SYN-0001",
                subject_token="CUST-SYN-000001",
                filed_at=datetime(2025, 3, 1, tzinfo=UTC),
                type="STR",
                ack_id="ACK-SYN-0001",
            ),
            PriorReport(
                report_id="RPT-SYN-0003",
                subject_token="CUST-SYN-000002",
                filed_at=datetime(2025, 6, 1, tzinfo=UTC),
                type="STR",
            ),
        ],
    )


def server(peer_min: int = 10) -> FastMCP:
    return build_server(
        repo(), guard("transactions"), tz=IST, max_rows=200, peer_min_accounts=peer_min
    )


def run(tool: str, srv: FastMCP | None = None, agent: str = "single_agent", **args: Any) -> Called:
    return call(srv or server(), tool, {"agent_token": token(agent), "case_id": CASE} | args)


# --------------------------------------------------------------------------- get_kyc_profile


@pytest.mark.tool("get_kyc_profile")
def test_get_kyc_profile_happy() -> None:
    by_account = run("get_kyc_profile", subject_token=ACCT)
    by_customer = run("get_kyc_profile", subject_token="CUST-SYN-000001")
    assert not by_account.is_error and by_account.result == by_customer.result
    assert by_account.result["declared_monthly_turnover"] == "50000.00"
    linked = run("get_kyc_profile", subject_token=LINKED)
    assert (linked.result["subject_token"], linked.result["segment"]) == (
        "CUST-SYN-000003",
        "salaried",
    )


@pytest.mark.tool("get_kyc_profile")
@pytest.mark.parametrize("subject", [OTHER_ACCT, "CUST-SYN-000002", EXTERNAL, "CUST-SYN-100001"])
def test_get_kyc_profile_out_of_scope(subject: str) -> None:
    assert run("get_kyc_profile", subject_token=subject).code == "OUT_OF_SCOPE"


@pytest.mark.tool("get_kyc_profile")
def test_get_kyc_profile_invalid_input() -> None:
    assert run("get_kyc_profile").code == "INVALID_INPUT"


# --------------------------------------------------------------------------- get_linked_accounts


@pytest.mark.tool("get_linked_accounts")
def test_get_linked_accounts_happy_both_directions() -> None:
    r = run("get_linked_accounts", subject_token="CUST-SYN-000001")
    assert not r.is_error and r.result["subject_token"] == "CUST-SYN-000001"
    assert r.result["links"] == [
        {
            "account_token": ACCT,
            "linked_account_token": EXTERNAL,
            "link_type": "device",
            "holder_token": None,
        },
        {
            "account_token": ACCT,
            "linked_account_token": LINKED,
            "link_type": "phone",
            "holder_token": "CUST-SYN-000003",
        },
    ]


@pytest.mark.tool("get_linked_accounts")
def test_get_linked_accounts_invalid_input_and_scope() -> None:
    assert run("get_linked_accounts").code == "INVALID_INPUT"
    assert run("get_linked_accounts", subject_token=OTHER_ACCT).code == "OUT_OF_SCOPE"


@pytest.mark.tool("get_transactions")
def test_linked_account_joins_the_scope_but_external_and_unlinked_do_not() -> None:
    def read(account: str) -> Called:
        return run(
            "get_transactions", account_token=account, from_date="2026-03-01", to_date="2026-03-31"
        )

    linked = read(LINKED)
    assert not linked.is_error and [t["amount"] for t in linked.result["transactions"]] == ["75.00"]
    assert read(EXTERNAL).code == "OUT_OF_SCOPE"  # not an account of this bank
    assert read(OTHER_ACCT).code == "OUT_OF_SCOPE"
    assert read("ACCT-SYN-000009").code == "OUT_OF_SCOPE"  # linked to another case's account


# --------------------------------------------------------------------------- get_prior_reports


@pytest.mark.tool("get_prior_reports")
def test_get_prior_reports_happy_oldest_first_and_own_subject_only() -> None:
    r = run("get_prior_reports", subject_token=ACCT)
    assert not r.is_error and r.result["subject_token"] == "CUST-SYN-000001"
    assert [(p["report_id"], p["type"], p["amount"]) for p in r.result["reports"]] == [
        ("RPT-SYN-0001", "STR", None),
        ("RPT-SYN-0002", "CTR", "1200000.00"),
    ]
    assert run("get_prior_reports", subject_token=LINKED).result["reports"] == []


@pytest.mark.tool("get_prior_reports")
def test_get_prior_reports_invalid_input_and_scope() -> None:
    assert run("get_prior_reports").code == "INVALID_INPUT"
    assert run("get_prior_reports", subject_token="CUST-SYN-000002").code == "OUT_OF_SCOPE"


# --------------------------------------------------------------------------- get_peer_baseline


@pytest.mark.tool("get_peer_baseline")
def test_get_peer_baseline_happy_exact_percentiles() -> None:
    r = run("get_peer_baseline", segment="salaried", window_days=31)
    assert not r.is_error
    res = r.result
    # peers: the 10 salaried accounts; not LINKED (in this case's scope), not the late joiner
    assert (res["peer_accounts"], res["from_date"], res["to_date"]) == (
        10,
        "2026-03-01",
        "2026-03-31",
    )
    # credits 100.00 .. 1000.00: nearest rank 5th = 500.00, 9th = 900.00
    assert res["credit_total"] == {"p50": "500.00", "p90": "900.00"}
    # debits: five 0.00 then 20.00, 40.00, 60.00, 80.00, 100.00 → 5th = 0.00, 9th = 80.00
    assert res["debit_total"] == res["cash_total"] == {"p50": "0.00", "p90": "80.00"}
    # counts: five accounts with 1, five with 2 → 5th = 1, 9th = 2
    assert res["txn_count"] == {"p50": 1, "p90": 2}
    assert "ACCT-SYN" not in str(res) and "CUST-SYN" not in str(res)  # aggregates only


@pytest.mark.tool("get_peer_baseline")
def test_get_peer_baseline_refuses_a_small_group() -> None:
    r = run("get_peer_baseline", server(peer_min=11), segment="salaried", window_days=31)
    assert r.code == "INVALID_INPUT" and "10 peer accounts" in r.result["message"]
    assert run("get_peer_baseline", segment="astronaut", window_days=31).code == "INVALID_INPUT"


@pytest.mark.tool("get_peer_baseline")
@pytest.mark.parametrize("window_days", [0, 367, "x"])
def test_get_peer_baseline_invalid_input(window_days: object) -> None:
    r = run("get_peer_baseline", segment="salaried", window_days=window_days)
    assert r.code == "INVALID_INPUT"


# --------------------------------------------------------------------------- tokens


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_kyc_profile", {"subject_token": ACCT}),
        ("get_linked_accounts", {"subject_token": ACCT}),
        ("get_prior_reports", {"subject_token": ACCT}),
        ("get_peer_baseline", {"segment": "salaried", "window_days": 31}),
    ],
)
def test_wrong_agent_token(tool: str, args: dict[str, Any]) -> None:
    assert run(tool, agent="researcher", **args).code == "FORBIDDEN"
