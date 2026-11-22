"""ingest_validate v2: coverage from balance reconciliation, quarantine and netting counts."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from auditguard.graph.nodes.ingest_validate import make_ingest_validate, quality_report
from mcp_servers.common.ledger import InMemoryLedgerRepo, booked_balances
from mcp_servers.transactions.server import build_server as build_tx
from tests.graph.world import IST, World
from tests.mcp_servers.helpers import ACCT, CASE, guard, ist, small_ledger, small_repo, txn

MIN = Decimal("0.80")


def quality(expected: int, bad: list[str], quarantined: int = 0, netted: int = 0) -> dict[str, Any]:
    return {
        "days_expected": expected,
        "days_reconciled": expected - len(bad),
        "unreconciled_days": bad,
        "quarantined": [{}] * quarantined,
        "netted": [{}] * netted,
        "unmatched_reversals": [],
        "rule_input_count": 1,
    }


def test_clean_slice_is_ok_with_full_coverage() -> None:
    r = quality_report(quality(60, []), txn_total=12, min_coverage=MIN)
    assert (r.verdict, r.coverage_ratio, r.missing_days) == ("ok", Decimal("1.0000"), [])


def test_unreconciled_days_are_a_gap_with_exact_coverage() -> None:
    r = quality_report(quality(60, ["2026-03-02", "2026-03-03"]), txn_total=12, min_coverage=MIN)
    # 58 / 60 = 0.96666… → 0.9667 (half-even to 4 places)
    assert (r.verdict, r.coverage_ratio) == ("gap", Decimal("0.9667"))
    assert r.missing_days == [date(2026, 3, 2), date(2026, 3, 3)]


def test_duplicates_and_reversals_alone_stay_ok() -> None:
    r = quality_report(quality(60, [], quarantined=2, netted=1), txn_total=12, min_coverage=MIN)
    assert (r.verdict, r.duplicates_quarantined, r.reversals_netted) == ("ok", 2, 1)


@pytest.mark.parametrize(
    ("expected", "bad", "total"),
    [
        (60, [f"2026-03-{d:02d}" for d in range(1, 14)], 12),  # 47/60 = 0.7833 < 0.80
        (60, [], 0),  # no rows at all
        (0, [], 5),  # nothing expected (account opened after the window)
    ],
)
def test_unusable(expected: int, bad: list[str], total: int) -> None:
    assert quality_report(quality(expected, bad), txn_total=total, min_coverage=MIN).verdict == (
        "unusable"
    )


def test_coverage_exactly_at_the_minimum_is_a_gap_not_unusable() -> None:
    bad = [f"2026-03-{d:02d}" for d in range(1, 13)]  # 48/60 = 0.8000
    assert quality_report(quality(60, bad), txn_total=1, min_coverage=MIN).verdict == "gap"


def run_ingest(world: World) -> dict[str, Any]:
    return asyncio.run(make_ingest_validate(world.deps())(world.start()))  # type: ignore[arg-type]


def test_node_on_a_clean_ledger(tmp_path: Path) -> None:
    out = run_ingest(World(tmp_path))
    q = out["data_quality"]
    assert (q.verdict, q.coverage_ratio, q.duplicates_quarantined) == ("ok", Decimal("1.0000"), 0)
    assert out["ledger"].txn_count == 7


def test_node_reports_the_gap_the_duplicate_and_the_reversal(tmp_path: Path) -> None:
    world = World(tmp_path)
    base = small_ledger()
    copy = base[0].model_copy(update={"txn_id": "TXN-SYN-0000020"})
    debit = txn(21, ist(30, 16), "700.00", "debit", "IMPS", "CPTY-SYN-000010")
    back = txn(22, ist(30, 17), "700.00", "credit", "IMPS", "CPTY-SYN-000010").model_copy(
        update={"reverses_txn_id": debit.txn_id}
    )
    books = booked_balances([*base, debit, back], IST)[ACCT]
    feed = [t for t in base if t.txn_id != "TXN-SYN-0000002"]  # 25 March lost in the feed
    repo = InMemoryLedgerRepo(
        alerts=[a for a in (small_repo().alert(CASE),) if a],
        transactions=[*feed, copy, debit, back],
        balances={ACCT: books},
        opened={ACCT: ist(1, 0)},  # opened 1 March: the window's February is not expected
    )
    world.servers["transactions"] = build_tx(repo, guard("transactions"), tz=IST, max_rows=200)
    out = run_ingest(world)
    q = out["data_quality"]
    assert (q.verdict, q.missing_days) == ("gap", [date(2026, 3, 25)])
    assert q.coverage_ratio == Decimal("0.9677")  # 30 / 31
    assert out["ledger"].txn_count == 6  # 9 rows on file - 1 quarantined - 2 netted
    assert (q.duplicates_quarantined, q.reversals_netted) == (1, 1)
