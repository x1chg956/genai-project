"""Links between background customers and reports filed on them earlier (`full` profile).

Feeds `ledger.link` and `ledger.prior_report` for get_linked_accounts and get_prior_reports.
Background accounts only: planted (alerted) accounts get no links or prior reports here, so a
repeat-subject or linked-account scenario is a typology decision for role 1.

Uses its own seeded stream (derived from the dataset seed), so adding or changing these
rates never changes a single transaction. Rates are in background_IN.yaml (`context`).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from auditguard.schemas import LinkedAccount, PriorReport
from data.generator.background import ContextSpec
from data.generator.model import AccountRow

STREAM = 0x11A7  # mixed into the seed: a stream separate from the transaction draws
LOOKBACK_DAYS = 730


def _pairs(rng: random.Random, pool: list[str], share: int) -> list[tuple[str, str]]:
    """Disjoint pairs covering about `share`% of the pool."""
    chosen = [a for a in pool if rng.randrange(100) < share]
    rng.shuffle(chosen)
    return [(chosen[i], chosen[i + 1]) for i in range(0, len(chosen) - 1, 2)]


def plant_context(
    accounts: list[AccountRow],
    background: list[str],
    spec: ContextSpec,
    seed: int,
    start: date,
    next_id: Callable[[str, int], str],
) -> tuple[list[LinkedAccount], list[PriorReport]]:
    rng = random.Random(seed ^ STREAM)  # noqa: S311 - synthetic data, not security
    pool = sorted(background)
    links: list[LinkedAccount] = []
    for a, b in _pairs(rng, pool, spec.phone_pair_percent):
        links.append(LinkedAccount(account_token=a, linked_account_token=b, link_type="phone"))
    for a, b in _pairs(rng, pool, spec.device_pair_percent):
        links.append(LinkedAccount(account_token=a, linked_account_token=b, link_type="device"))
    for a in pool:
        if rng.randrange(100) < spec.introducer_percent:
            introducer = rng.choice([x for x in pool if x != a])
            links.append(
                LinkedAccount(
                    account_token=a, linked_account_token=introducer, link_type="introducer"
                )
            )
        if rng.randrange(100) < spec.external_link_percent:
            links.append(
                LinkedAccount(
                    account_token=a, linked_account_token=next_id("ACCT-EXT", 6), link_type="phone"
                )
            )
    unique = {(x.account_token, x.linked_account_token, x.link_type): x for x in links}

    holder = {a.account_token: a.customer_token for a in accounts}
    reports: list[PriorReport] = []

    def filed() -> datetime:
        day = start - timedelta(days=rng.randint(30, LOOKBACK_DAYS))
        return datetime.combine(day, time(rng.randint(4, 12), 0), tzinfo=UTC)

    for a in pool:
        if rng.randrange(100) < spec.prior_ctr_percent:
            amount = Decimal(rng.randint(*spec.ctr_amount)).quantize(Decimal("0.01"))
            reports.append(
                PriorReport(
                    report_id=next_id("RPT", 6),
                    subject_token=holder[a],
                    filed_at=filed(),
                    type="CTR",
                    amount=amount,
                    ack_id=next_id("ACK", 6),
                )
            )
        if rng.randrange(1000) < spec.prior_str_per_mille:
            reports.append(
                PriorReport(
                    report_id=next_id("RPT", 6),
                    subject_token=holder[a],
                    filed_at=filed(),
                    type="STR",
                    ack_id=next_id("ACK", 6),
                )
            )
    return list(unique.values()), reports
