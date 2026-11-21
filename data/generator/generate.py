"""Seeded synthetic ledger generator (docs/04 §2). Pure: same inputs give the same dataset.

Layout of one run: one customer and one savings account per account slot. Planted scenarios
take the first slots in spec order (entry order, then variant order), so ALR-SYN-0001 is the
first variant of the first entry. The remaining slots get ordinary background activity.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from auditguard.schemas import Alert, Transaction
from auditguard.schemas.ledger import Channel, Direction
from data.generator.model import (
    AccountRow,
    CaseLabel,
    CustomerRow,
    Dataset,
    PartyRow,
    ScenarioParams,
    TxnRow,
)
from data.generator.spec import Range, TypologySpec

IST = ZoneInfo("Asia/Kolkata")
HERE = Path(__file__).resolve().parent
NAMES_FILE = HERE / "names_fictional.txt"
DENY_FILE = HERE / "names_denylist.txt"
CENT = Decimal("0.01")

BENIGN_MEMOS = (
    "groceries",
    "rent",
    "electricity bill",
    "mobile recharge",
    "fuel",
    "pharmacy",
    "school fees",
    "restaurant",
    "transfer",
    "household",
)


@dataclass(frozen=True)
class Profile:
    name: str
    accounts: int
    days: int


PROFILES = {"tiny": Profile("tiny", accounts=10, days=60)}


class GeneratorError(ValueError):
    """The spec or profile cannot produce a valid dataset."""


def load_names() -> list[str]:
    names = [n.strip() for n in NAMES_FILE.read_text(encoding="utf-8").splitlines() if n.strip()]
    deny = {
        d.strip().casefold()
        for d in DENY_FILE.read_text(encoding="utf-8").splitlines()
        if d.strip() and not d.startswith("#")
    }
    clashes = [n for n in names if n.casefold() in deny]
    if clashes:
        raise GeneratorError(f"names on the deny-list: {clashes}")
    if len(set(names)) != len(names):
        raise GeneratorError("names_fictional.txt has duplicates")
    return names


class Ids:
    """Sequential, clearly fake identifiers."""

    def __init__(self) -> None:
        self._n: dict[str, int] = {}

    def next(self, prefix: str, width: int) -> str:
        self._n[prefix] = self._n.get(prefix, 0) + 1
        return f"{prefix}-SYN-{self._n[prefix]:0{width}d}"


class Draw:
    """Random draws from spec ranges. Money is drawn in whole paise, never as a float."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    def integer(self, r: Range) -> int:
        return self.rng.randint(int(r.low), int(r.high))

    def money(self, r: Range) -> Decimal:
        paise = self.rng.randint(int(r.low * 100), int(r.high * 100))
        return (Decimal(paise) / 100).quantize(CENT)

    def share(self, r: Range) -> Decimal:
        bp = self.rng.randint(int(r.low * 10000), int(r.high * 10000))
        return Decimal(bp) / 10000

    def hours(self, r: Range) -> Decimal:
        minutes = self.rng.randint(int(r.low * 60), int(r.high * 60))
        return Decimal(minutes) / 60


def local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=IST).astimezone(UTC)


class Builder:
    def __init__(self, dataset: Dataset, ids: Ids, rng: random.Random) -> None:
        self.ds, self.ids, self.rng = dataset, ids, rng

    def txn(
        self,
        account: str,
        ts: datetime,
        amount: Decimal,
        direction: Direction,
        channel: Channel,
        counterparty: str | None,
        memo: str | None = None,
    ) -> Transaction:
        txn_id = self.ids.next("TXN", 7)
        t = Transaction(
            txn_id=txn_id,
            account_token=account,
            ts=ts,
            amount=amount.quantize(CENT, rounding=ROUND_HALF_EVEN),
            direction=direction,
            channel=channel,
            counterparty_token=counterparty,
            memo_ref=f"MEMO-{txn_id}" if memo else None,
        )
        self.ds.transactions.append(TxnRow(t, memo))
        return t


# --------------------------------------------------------------------------- background


def background(b: Builder, account: str, start: date, days: int, *, light: bool) -> None:
    """Ordinary activity. Light background (planted accounts) has a few small debits only."""
    rng, draw = b.rng, Draw(b.rng)
    employer = b.ids.next("CPTY", 6)
    landlord = b.ids.next("CPTY", 6)
    merchants = [b.ids.next("CPTY", 6) for _ in range(8)]
    salary = draw.money(Range(low=Decimal(30000), high=Decimal(120000)))
    for offset in range(days):
        day = start + timedelta(days=offset)
        if not light and day.day == 1:
            b.txn(account, local(day, 10), salary, "credit", "NEFT", employer, "salary")
        if not light and day.day == 5:
            rent = (salary * Decimal("0.3")).quantize(CENT)
            b.txn(account, local(day, 11), rent, "debit", "NEFT", landlord, "rent")
        for _ in range(rng.randint(0, 1 if light else 3)):
            amount = draw.money(Range(low=Decimal(50), high=Decimal(2500)))
            hour = rng.randint(8, 22)
            memo = rng.choice(BENIGN_MEMOS)
            b.txn(
                account,
                local(day, hour, rng.randint(0, 59)),
                amount,
                "debit",
                "UPI",
                rng.choice(merchants),
                memo,
            )
        if not light and rng.random() < 0.05:
            amount = draw.money(Range(low=Decimal(2000), high=Decimal(10000)))
            b.txn(account, local(day, 18), amount, "debit", "CASH", None, None)


# --------------------------------------------------------------------------- planters


@dataclass(frozen=True)
class Planted:
    txn_ids: list[str]
    last_ts: datetime
    drawn: dict[str, str]


Planter = Callable[[Builder, str, date, dict[str, Range]], Planted]


def plant_rapid_pass_through(b: Builder, account: str, start: date, p: dict[str, Range]) -> Planted:
    draw = Draw(b.rng)
    n = draw.integer(p["inflow_count"])
    n_senders = draw.integer(p["distinct_senders"]) if "distinct_senders" in p else n
    n_payees = draw.integer(p["outflow_beneficiaries"]) if "outflow_beneficiaries" in p else 1
    span = draw.integer(p.get("span_days", Range(low=Decimal(3), high=Decimal(10))))
    share = draw.share(p["share_out"])
    senders = [b.ids.next("CPTY", 6) for _ in range(n_senders)]
    payees = [b.ids.next("CPTY", 6) for _ in range(n_payees)]
    parts = max(1, n_payees // n)
    bulk = n == 1  # one large credit split into many payouts, as a payroll account does
    ids: list[str] = []
    last = local(start, 0)
    payee_i = 0
    for i in range(n):
        day = start + timedelta(days=b.rng.randint(0, max(span - 1, 0)))
        ts = local(day, b.rng.randint(8, 21), b.rng.randint(0, 59))
        amount = draw.money(p["inflow_amount"])
        credit_channel: Channel = "NEFT" if bulk else b.rng.choice(("UPI", "IMPS"))
        credit = b.txn(
            account,
            ts,
            amount,
            "credit",
            credit_channel,
            senders[i % n_senders],
            "salary funding" if bulk else "transfer",
        )
        ids.append(credit.txn_id)
        forward = (amount * share).quantize(CENT)
        portions = _split(b.rng, forward, parts)
        for portion in portions:
            hold = timedelta(hours=float(draw.hours(p["hold_hours"])))
            debit = b.txn(
                account,
                ts + hold,
                portion,
                "debit",
                "NEFT" if bulk else "IMPS",
                payees[payee_i % n_payees],
                "salary" if bulk else "transfer",
            )
            payee_i += 1
            ids.append(debit.txn_id)
            last = max(last, debit.ts)
    drawn = {
        "inflow_count": str(n),
        "distinct_senders": str(n_senders),
        "outflow_beneficiaries": str(n_payees),
        "span_days": str(span),
        "share_out": str(share),
    }
    return Planted(ids, last, drawn)


def plant_fan_in(b: Builder, account: str, start: date, p: dict[str, Range]) -> Planted:
    draw = Draw(b.rng)
    n = draw.integer(p["sender_count"])
    window = draw.integer(p["window_days"])
    ids: list[str] = []
    total = Decimal("0.00")
    last = local(start, 0)
    for _ in range(n):
        day = start + timedelta(days=b.rng.randint(0, max(window - 1, 0)))
        ts = local(day, b.rng.randint(8, 21), b.rng.randint(0, 59))
        credit = b.txn(
            account,
            ts,
            draw.money(p["inflow_amount"]),
            "credit",
            "UPI",
            b.ids.next("CPTY", 6),
            "transfer",
        )
        ids.append(credit.txn_id)
        total += credit.amount
        last = max(last, credit.ts)
    drawn = {"sender_count": str(n), "window_days": str(window)}
    if "hold_days" in p:
        hold = draw.integer(p["hold_days"])
        out_day = start + timedelta(days=window - 1 + hold)
        debit = b.txn(
            account, local(out_day, 12), total, "debit", "NEFT", b.ids.next("CPTY", 6), "transfer"
        )
        ids.append(debit.txn_id)
        last = debit.ts
        drawn["hold_days"] = str(hold)
    return Planted(ids, last, drawn)


PLANTERS: dict[str, Planter] = {
    "rapid_pass_through": plant_rapid_pass_through,
    "fan_in": plant_fan_in,
}


def _split(rng: random.Random, amount: Decimal, parts: int) -> list[Decimal]:
    """Split an amount into `parts` positive pieces that sum exactly to it."""
    if parts == 1:
        return [amount]
    paise = int(amount * 100)
    cuts = sorted(rng.sample(range(1, paise), parts - 1))
    edges = [0, *cuts, paise]
    return [(Decimal(edges[i + 1] - edges[i]) / 100).quantize(CENT) for i in range(parts)]


def _needed_days(p: dict[str, Range]) -> int:
    span = int(
        p.get("span_days", p.get("window_days", Range(low=Decimal(1), high=Decimal(10)))).high
    )
    hold = int(p["hold_hours"].high) if "hold_hours" in p else 0
    hold_days = int(p["hold_days"].high) if "hold_days" in p else 0
    return span + math.ceil(hold / 24) + hold_days + 2


# --------------------------------------------------------------------------- entry point


def spec_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(
    spec: TypologySpec,
    *,
    seed: int,
    profile: str = "tiny",
    end_date: date = date(2026, 3, 31),
    spec_hash: str = "",
) -> Dataset:
    if profile not in PROFILES:
        raise GeneratorError(f"unknown profile {profile!r}; known: {sorted(PROFILES)}")
    prof = PROFILES[profile]
    rng = random.Random(seed)  # noqa: S311 - synthetic data, not security
    ids = Ids()
    ds = Dataset(seed=seed, profile=profile)
    b = Builder(ds, ids, rng)
    start = end_date - timedelta(days=prof.days - 1)
    names = load_names()
    if len(names) < prof.accounts:
        raise GeneratorError("not enough fictional names for this profile")
    chosen_names = rng.sample(names, prof.accounts)

    plants = [(e, v) for e in spec.entries for v in e.variants]
    if len(plants) > prof.accounts:
        raise GeneratorError(f"{len(plants)} planted scenarios do not fit {prof.accounts} accounts")

    for slot in range(prof.accounts):
        customer = ids.next("CUST", 6)
        account = ids.next("ACCT", 6)
        planted = slot < len(plants)
        opened = start - timedelta(days=rng.randint(20, 90) if planted else rng.randint(400, 3000))
        ds.customers.append(
            CustomerRow(
                customer_token=customer,
                segment="salaried",
                kyc_risk_band="low" if not planted else rng.choice(("low", "medium")),
                declared_monthly_turnover=Draw(rng).money(
                    Range(low=Decimal(40000), high=Decimal(150000))
                ),
                onboarded_at=local(opened, 11),
            )
        )
        ds.parties.append(PartyRow(ids.next("PARTY", 6), customer, chosen_names[slot], "holder"))
        ds.accounts.append(AccountRow(account, customer, "savings", local(opened, 11), "active"))
        background(b, account, start, prof.days, light=planted)
        if not planted:
            continue

        entry, variant = plants[slot]
        params = entry.resolved(variant)
        latest_start = prof.days - _needed_days(params)
        if latest_start < 5:
            raise GeneratorError(f"{entry.scenario_family}/{variant} does not fit {prof.days} days")
        plant_start = start + timedelta(days=rng.randint(5, latest_start))
        result = PLANTERS[entry.scenario_family](b, account, plant_start, params)
        alert_id = ids.next("ALR", 4)
        detected = (result.last_ts + timedelta(days=1)).astimezone(IST).date()
        ds.alerts.append(
            Alert(
                alert_id=alert_id,
                account_ref=account,
                scenario_code=f"TM-{entry.scenario_family.upper()}",
                detected_at=local(detected, 9),
            )
        )
        ds.labels.append(
            CaseLabel(
                alert_id=alert_id,
                account_token=account,
                scenario_family=entry.scenario_family,
                variant=variant,
                expected_disposition=entry.variants[variant].expected_disposition,
                planted_txn_ids=result.txn_ids,
                seed=seed,
                spec_sha256=spec_hash,
            )
        )
        ds.scenario_params.append(
            ScenarioParams(alert_id, {"plant_start": plant_start.isoformat(), **result.drawn})
        )

    ds.transactions.sort(key=lambda r: (r.txn.ts, r.txn.txn_id))
    return ds
