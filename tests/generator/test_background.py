"""The `full` profile and its realistic background (docs/04 §2)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

import data.generator.generate as gen_module
from auditguard.config import REPO_ROOT
from data.generator.background import (
    Bucket,
    _count,
    last_working_day,
    load_background,
    ticket,
)
from data.generator.generate import GeneratorError, generate, name_pool
from data.generator.model import Dataset
from data.generator.spec import load_spec

IST = ZoneInfo("Asia/Kolkata")
SPEC = load_spec(REPO_ROOT / "data" / "generator" / "typology_spec.yaml")


@pytest.fixture(scope="module")
def full() -> Dataset:
    return generate(SPEC, seed=7, profile="full", spec_hash="h")


def _background(ds: Dataset) -> list[Any]:
    alerted = {a.account_ref for a in ds.alerts}
    return [r.txn for r in ds.transactions if r.txn.account_token not in alerted]


def test_full_profile_shape(full: Dataset) -> None:
    assert len(full.customers) == len(full.accounts) == len(full.parties) == 320
    variants = sum(len(e.variants) for e in SPEC.entries)
    assert len(full.alerts) == len(full.labels) == 4 * variants
    assert len({p.display_name for p in full.parties}) == 320
    assert {c.segment for c in full.customers} == {
        "salaried",
        "self_employed",
        "student",
        "pensioner",
    }


def test_full_profile_is_deterministic(full: Dataset) -> None:
    again = generate(SPEC, seed=7, profile="full", spec_hash="h")
    assert [r.txn for r in again.transactions[:2000]] == [r.txn for r in full.transactions[:2000]]
    assert len(again.transactions) == len(full.transactions)
    assert again.labels == full.labels


def test_alert_order_does_not_follow_variant_order(full: Dataset) -> None:
    variants = [lab.variant for lab in full.labels]
    in_spec_order = sorted(variants, key=[v for e in SPEC.entries for v in e.variants].index)
    assert variants != in_spec_order


def test_channel_mix_has_every_retail_channel(full: Dataset) -> None:
    channels = Counter(t.channel for t in _background(full))
    assert {"UPI", "NEFT", "IMPS", "CASH", "CARD"} <= set(channels)
    assert channels["UPI"] / sum(channels.values()) > 0.6  # UPI dominates retail volume


def test_average_tickets_near_published_figures(full: Dataset) -> None:
    """Within 20% of the RBI/Worldline figures cited in background_IN.yaml."""
    sums: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    counts: Counter[tuple[str, str]] = Counter()
    for t in _background(full):
        sums[(t.channel, t.direction)] += t.amount
        counts[(t.channel, t.direction)] += 1
    published = {
        ("CARD", "debit"): Decimal(2776),
        ("CASH", "debit"): Decimal(5079),
        ("IMPS", "debit"): Decimal(12700),
    }
    for key, figure in published.items():
        mean = sums[key] / counts[key]
        assert abs(mean - figure) / figure < Decimal("0.20"), (key, mean)


def test_money_is_decimal_to_the_paisa(full: Dataset) -> None:
    for t in full.transactions[:5000]:
        assert isinstance(t.txn.amount, Decimal)
        assert t.txn.amount == t.txn.amount.quantize(Decimal("0.01"))
        assert t.txn.amount > 0


def test_atm_withdrawals_are_multiples_of_500(full: Dataset) -> None:
    cash = [t.amount for t in _background(full) if t.channel == "CASH" and t.direction == "debit"]
    assert cash and all(a % 500 == 0 for a in cash)


def test_festival_week_has_more_spending(full: Dataset) -> None:
    per_day = Counter(
        t.ts.astimezone(IST).date()
        for t in _background(full)
        if t.channel in ("UPI", "CARD") and t.direction == "debit"
    )
    assert per_day[date(2025, 10, 19)] > 1.4 * per_day[date(2025, 11, 12)]


def test_salaries_land_on_the_1st_or_last_working_day(full: Dataset) -> None:
    days = {
        t.ts.astimezone(IST).date()
        for r in full.transactions
        if (t := r.txn).channel == "NEFT" and t.direction == "credit" and r.memo == "salary"
    }
    assert days
    for d in days:
        assert d.day == 1 or d == last_working_day(d.year, d.month)


def test_tiny_profile_unchanged_by_full() -> None:
    tiny = generate(SPEC, seed=42, spec_hash="h")
    assert len(tiny.accounts) == 10
    assert len(tiny.alerts) == sum(len(e.variants) for e in SPEC.entries)
    assert {a.type for a in tiny.accounts} == {"savings"}


def test_name_pool_pairs_fictional_parts_and_respects_deny_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    names = tmp_path / "names.txt"
    names.write_text("Avelor Quellan\nRatan Morrikane\nCirrath Tata\n")
    deny = tmp_path / "deny.txt"
    deny.write_text("# real\nRatan Tata\n")
    monkeypatch.setattr(gen_module, "NAMES_FILE", names)
    monkeypatch.setattr(gen_module, "DENY_FILE", deny)
    pool = name_pool(8)
    assert "Ratan Tata" not in pool
    assert len(pool) == len(set(pool)) == 8
    with pytest.raises(GeneratorError, match="fictional names"):
        name_pool(9)


def test_ticket_draws_stay_in_buckets() -> None:
    import random

    rng = random.Random(1)  # noqa: S311 - test data
    buckets = [Bucket(low=10, high=20, weight=1), Bucket(low=100, high=100, weight=1)]
    for _ in range(200):
        a = ticket(rng, buckets)
        assert Decimal(10) <= a <= Decimal(20) or a == Decimal(100)


def test_daily_count_matches_monthly_rate() -> None:
    import random

    rng = random.Random(3)  # noqa: S311 - test data
    total = sum(_count(rng, 30, 100) for _ in range(3000))
    assert 2900 <= total <= 3100  # 30 a month at scale 100 is 1 a day
    assert _count(rng, 0, 100) == 0
    assert _count(rng, 60, 100) == 2  # exactly 2 a day, no randomness left


def test_background_spec_rejects_bad_buckets(tmp_path: Path) -> None:
    text = (REPO_ROOT / "data" / "generator" / "background_IN.yaml").read_text()
    bad = tmp_path / "bg.yaml"
    bad.write_text(text.replace("- [20, 200, 50]", "- [200, 20, 50]"))
    with pytest.raises(ValidationError, match="high"):
        load_background(bad)
    assert load_background().version == 1


def test_last_working_day() -> None:
    assert last_working_day(2026, 1) == date(2026, 1, 30)  # 31 Jan 2026 is a Saturday
    assert last_working_day(2025, 12) == date(2025, 12, 31)


def test_defects_are_planted_on_background_accounts_only(full: Dataset) -> None:
    kinds = Counter(d.kind for d in full.defects)
    assert set(kinds) == {"missing_days", "double_post", "reversal"}
    alerted = {a.account_ref for a in full.alerts}
    assert not {d.account_token for d in full.defects} & alerted


def test_missing_days_leave_a_gap(full: Dataset) -> None:
    gap = next(d for d in full.defects if d.kind == "missing_days")
    start, end = date.fromisoformat(gap.detail["from"]), date.fromisoformat(gap.detail["to"])
    days = {
        r.txn.ts.astimezone(IST).date()
        for r in full.transactions
        if r.txn.account_token == gap.account_token
    }
    assert not {d for d in days if start <= d <= end}
    assert (end - start).days + 1 == int(gap.detail["days"])


def test_double_posts_and_reversals_match_their_originals(full: Dataset) -> None:
    by_id = {r.txn.txn_id: r.txn for r in full.transactions}
    for d in full.defects:
        if d.kind == "missing_days":
            continue
        first, second = (by_id[i] for i in d.txn_ids)
        assert first.amount == second.amount == Decimal(d.detail["amount"])
        assert first.counterparty_token == second.counterparty_token
        if d.kind == "double_post":
            assert (first.ts, first.direction, first.channel) == (
                second.ts,
                second.direction,
                second.channel,
            )
        else:
            assert (first.direction, second.direction) == ("debit", "credit")
            assert second.ts > first.ts


def test_defects_reach_the_groundtruth_rows(full: Dataset) -> None:
    from data.generator.writer import label_rows

    rows = next(v for k, v in label_rows(full).items() if k.startswith("data_defects"))
    assert len(rows) == len(full.defects) and {len(r) for r in rows} == {4}
