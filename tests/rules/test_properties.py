"""Property tests for every rule over generated ledgers (docs/02 Phase 2 task 2).

Each property runs on 500 ledgers, derandomised so CI and laptops see the same cases. Rules
run with their real parameters and with scaled-down ones, so the "fires" branch is exercised
on small ledgers too. The brute-force oracles are written separately from the rule code.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from auditguard.schemas import KycProfile, RuleResult, Transaction
from mcp_servers.aml_engine.engine.registry import LoadedRule
from tests.rules.helpers import ACCOUNT, IST, pack

PACK = pack()
EXAMPLES = settings(
    max_examples=500,
    derandomize=True,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.too_slow],
)
BASE = datetime(2025, 1, 1, 0, 0, tzinfo=IST)
SMALL = {  # thresholds scaled so small ledgers can fire
    "R01": {"threshold": Decimal("10000.00"), "lower_ratio": Decimal("0.10"), "min_count": 2},
    "R02": {"threshold": Decimal("10000.00")},
    "R03": {"min_baseline_blocks": 2},
    "R04": {"residual_balance_max": Decimal("5000.00")},
    "R05": {"dormant_days": 20, "lookback_days": 60, "reactivation_days": 10},
    "R06": {"multiple": Decimal("1")},
    "R07": {"min_senders": 3, "window_days": 3},
    "R08": {"min_beneficiaries": 3, "window_days": 3},
}
KYC = KycProfile(
    subject_token="CUST-SYN-000001",
    segment="salaried",
    kyc_risk_band="low",
    declared_monthly_turnover=Decimal("20000.00"),
    onboarded_at=datetime(2024, 1, 1, tzinfo=IST),
)


@st.composite
def ledgers(draw: Any) -> list[Transaction]:
    n = draw(st.integers(min_value=0, max_value=40))
    span_days = draw(st.sampled_from([10, 60, 400]))
    out = []
    for i in range(n):
        minutes = draw(st.integers(min_value=0, max_value=span_days * 24 * 60))
        out.append(
            Transaction(
                txn_id=f"TXN-SYN-{i:07d}",
                account_token=ACCOUNT,
                ts=BASE + timedelta(minutes=minutes),
                amount=Decimal(draw(st.integers(min_value=1, max_value=1_500_000))) / 100,
                direction=draw(st.sampled_from(["credit", "debit"])),
                channel=draw(st.sampled_from(["UPI", "NEFT", "IMPS", "CASH", "CARD"])),
                counterparty_token=draw(
                    st.one_of(st.none(), st.sampled_from([f"CPTY-SYN-{k:06d}" for k in range(8)]))
                ),
            )
        )
    return out


def variants(rule_id: str) -> list[tuple[str, LoadedRule]]:
    rule = PACK.rules[rule_id]
    small = rule.params.model_copy(update=SMALL[rule_id])
    return [("default", rule), ("small", LoadedRule(rule.meta, small, rule.evaluate))]


def evaluate(rule: LoadedRule, ledger: list[Transaction]) -> RuleResult:
    return rule.evaluate(ledger, KYC, rule.params, IST)


RULES = sorted(PACK.rules)


@EXAMPLES
@given(ledger=ledgers(), seed=st.integers(min_value=0, max_value=2**32))
def test_every_rule_is_order_independent_and_well_formed(
    ledger: list[Transaction], seed: int
) -> None:
    shuffled = list(ledger)
    random.Random(seed).shuffle(shuffled)  # noqa: S311 - test data
    ids = {t.txn_id for t in ledger}
    for rule_id in RULES:
        for _, rule in variants(rule_id):
            result = evaluate(rule, ledger)
            assert result == evaluate(rule, shuffled), rule_id
            assert result.rule_id == rule_id
            assert result.score >= 0
            assert set(result.supporting_txn_ids) <= ids
            assert len(set(result.supporting_txn_ids)) == len(result.supporting_txn_ids)


@EXAMPLES
@given(ledger=ledgers())
def test_fired_agrees_with_reported_inputs(ledger: list[Transaction]) -> None:
    for rule_id in RULES:
        for _, rule in variants(rule_id):
            r = evaluate(rule, ledger)
            p, i = rule.params, r.inputs
            if rule_id == "R01":
                assert r.fired == (int(i["qualifying_deposits_in_window"]) >= p.min_count)
            elif rule_id == "R02":
                assert r.fired == (Decimal(i["month_total"]) >= p.threshold)
            elif rule_id == "R03" and r.fired:
                k = p.multiple
                assert Decimal(i["block_count"]) > k * Decimal(i["baseline_p90_count"]) or (
                    Decimal(i["block_value"]) > k * Decimal(i["baseline_p90_value"])
                )
            elif rule_id == "R04" and r.fired:
                assert Decimal(i["residual_balance"]) <= p.residual_balance_max
                assert Decimal(i["total_credits"]) > 0
            elif rule_id == "R05":
                limit = i["limit"]
                assert r.fired == (
                    bool(limit) and Decimal(i["reactivation_value"]) > Decimal(limit)
                )
            elif rule_id == "R06":
                assert r.fired == (Decimal(i["credit_turnover"]) > Decimal(i["limit"]))
            elif rule_id == "R07":
                assert r.fired == (int(i["distinct_senders_in_window"]) >= p.min_senders)
            elif rule_id == "R08":
                assert r.fired == (
                    int(i["distinct_beneficiaries_in_window"]) >= p.min_beneficiaries
                )


def _brute_distinct(ledger: list[Transaction], direction: str, days: int) -> int:
    """Most distinct counterparties in any `days` local-day window, by trying every start day."""
    rows = [t for t in ledger if t.direction == direction and t.counterparty_token]
    if not rows:
        return 0
    dates = [t.ts.astimezone(IST).date() for t in rows]
    best = 0
    day = min(dates)
    while day <= max(dates):
        end = day + timedelta(days=days - 1)
        best = max(
            best,
            len(
                {t.counterparty_token for t, d in zip(rows, dates, strict=True) if day <= d <= end}
            ),
        )
        day += timedelta(days=1)
    return best


def _brute_cash_month(ledger: list[Transaction], threshold: Decimal) -> Decimal:
    sums: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0.00"))
    for t in ledger:
        if t.channel == "CASH" and t.amount < threshold:
            sums[(t.ts.astimezone(IST).strftime("%Y-%m"), t.direction)] += t.amount
    return max(sums.values(), default=Decimal("0.00"))


@EXAMPLES
@given(ledger=ledgers())
def test_fan_rules_and_cash_months_match_brute_force(ledger: list[Transaction]) -> None:
    for _, rule in variants("R07"):
        got = int(evaluate(rule, ledger).inputs["distinct_senders_in_window"])
        assert got == _brute_distinct(ledger, "credit", rule.params.window_days)
    for _, rule in variants("R08"):
        got = int(evaluate(rule, ledger).inputs["distinct_beneficiaries_in_window"])
        assert got == _brute_distinct(ledger, "debit", rule.params.window_days)
    for _, rule in variants("R02"):
        got = Decimal(evaluate(rule, ledger).inputs["month_total"])
        assert got == _brute_cash_month(ledger, rule.params.threshold)


@EXAMPLES
@given(ledger=ledgers(), extra=ledgers())
def test_adding_transactions_never_unfires_count_rules(
    ledger: list[Transaction], extra: list[Transaction]
) -> None:
    more = ledger + [t.model_copy(update={"txn_id": f"{t.txn_id}-X"}) for t in extra]
    for rule_id in ("R01", "R02", "R07", "R08"):
        for _, rule in variants(rule_id):
            if evaluate(rule, ledger).fired:
                assert evaluate(rule, more).fired, rule_id


def test_small_variants_do_fire_sometimes() -> None:
    """Guard against vacuous properties: every scaled rule fires on some random ledger."""
    fired: set[str] = set()

    @settings(max_examples=500, derandomize=True, deadline=None, database=None)
    @given(ledger=ledgers())
    def probe(ledger: list[Transaction]) -> None:
        for rule_id in RULES:
            if evaluate(variants(rule_id)[1][1], ledger).fired:
                fired.add(rule_id)

    probe()
    assert fired == set(RULES)
