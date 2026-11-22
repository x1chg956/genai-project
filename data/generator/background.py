"""Realistic background activity for the `full` profile (docs/04 §2), from background_IN.yaml.

Pure and seeded like the rest of the generator. Customers are salaried, self-employed (a
small shop paid by UPI), students or pensioners. Each one gets income on its usual day,
spending across UPI, card, ATM cash, IMPS and NEFT with ticket sizes from public payment
statistics (sources in the YAML), and more spending in the week before a festival.
Counterparties come from bank-wide pools, so many customers pay the same merchants.

Nothing here is planted or labelled. Every amount is drawn in whole paise.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from auditguard.schemas.ledger import Channel

if TYPE_CHECKING:
    from data.generator.generate import Builder

IST_NAME = "Asia/Kolkata"
BACKGROUND_FILE = Path(__file__).resolve().parent / "background_IN.yaml"
CENT = Decimal("0.01")
Segment = Literal["salaried", "self_employed", "student", "pensioner"]
TICKETS = ("upi_p2m", "upi_p2p", "card_pos", "atm_cash", "imps", "shop_sale")
SPEND_KINDS = ("upi_p2m", "upi_p2p", "card_pos", "atm_cash", "imps")
SPEND_MEMOS = {
    "upi_p2m": ("groceries", "fuel", "pharmacy", "restaurant", "mobile recharge", "household"),
    "upi_p2p": ("transfer", "split bill", "family", "repayment"),
    "card_pos": ("shopping", "groceries", "fuel", "electronics", "clothing"),
    "imps": ("transfer", "family support", "school fees"),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Bucket(_Strict):
    low: int = Field(gt=0)
    high: int
    weight: int = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> Bucket:
        if self.high < self.low:
            raise ValueError(f"bucket high {self.high} < low {self.low}")
        return self


class Festival(_Strict):
    name: str
    date: date


class BackgroundSpec(_Strict):
    version: int
    segments: dict[Segment, int]
    tickets: dict[str, list[Bucket]]
    monthly: dict[Segment, dict[str, int]]
    income: dict[str, list[int] | int]
    festival_uplift_percent: int = Field(ge=100)
    festivals: list[Festival]

    @model_validator(mode="after")
    def _complete(self) -> BackgroundSpec:
        missing = [t for t in TICKETS if not self.tickets.get(t)]
        if missing:
            raise ValueError(f"tickets missing {missing}")
        for segment, counts in self.monthly.items():
            unknown = set(counts) - set(SPEND_KINDS)
            if unknown:
                raise ValueError(f"monthly.{segment}: unknown kinds {sorted(unknown)}")
        return self

    def span(self, key: str) -> tuple[int, int]:
        value = self.income[key]
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError(f"income.{key} must be [low, high]")
        return value[0], value[1]

    def percent(self, key: str) -> int:
        value = self.income[key]
        if not isinstance(value, int):
            raise ValueError(f"income.{key} must be an integer percent")
        return value


def load_background(path: Path = BACKGROUND_FILE) -> BackgroundSpec:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["tickets"] = {
        k: [{"low": b[0], "high": b[1], "weight": b[2]} for b in v]
        for k, v in raw.get("tickets", {}).items()
    }
    return BackgroundSpec.model_validate(raw)


@dataclass(frozen=True)
class Pools:
    """Bank-wide counterparty tokens shared by every background customer."""

    merchants: list[str]
    employers: list[str]
    peers: list[str]
    suppliers: list[str]
    shoppers: list[str]

    @classmethod
    def build(cls, next_id: Callable[[str, int], str], accounts: int) -> Pools:
        def take(n: int) -> list[str]:
            return [next_id("CPTY", 6) for _ in range(n)]

        return cls(
            merchants=take(max(50, accounts * 2)),
            employers=take(max(10, accounts // 8)),
            peers=take(max(100, accounts * 6)),
            suppliers=take(max(20, accounts // 3)),
            shoppers=take(max(200, accounts * 10)),
        )


@dataclass(frozen=True)
class Plan:
    """One background customer, chosen before any of its transactions."""

    segment: Segment
    monthly_income: Decimal
    spend_scale: int  # percent of the salaried reference customer
    declared_monthly_turnover: Decimal
    account_type: str


def paise(rng: random.Random, low: int, high: int) -> Decimal:
    return (Decimal(rng.randint(low * 100, high * 100)) / 100).quantize(CENT)


def ticket(rng: random.Random, buckets: list[Bucket]) -> Decimal:
    bucket = rng.choices(buckets, weights=[b.weight for b in buckets])[0]
    return paise(rng, bucket.low, bucket.high)


def _local(day: date, hour: int, minute: int) -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(IST_NAME)).astimezone(UTC)


def _count(rng: random.Random, per_month: int, scale_pct: int) -> int:
    """Today's count for a monthly rate, in integers: rate * scale% / 30 days, rounded at random."""
    units = per_month * scale_pct  # per 30 days, in hundredths
    whole, part = divmod(units, 3000)
    return whole + (1 if rng.randrange(3000) < part else 0)


def plan_customer(rng: random.Random, spec: BackgroundSpec) -> Plan:
    segments = list(spec.segments)
    segment = rng.choices(segments, weights=[spec.segments[s] for s in segments])[0]
    if segment == "salaried":
        income = paise(rng, *spec.span("salary"))
    elif segment == "pensioner":
        income = paise(rng, *spec.span("pension"))
    elif segment == "student":
        income = paise(rng, *spec.span("allowance"))
    else:
        low, high = spec.span("shop_sales_per_month")
        income = Decimal((low + high) // 2 * 600)  # rough sales value, for the spend scale
    scale = max(40, min(200, int(income / 600)))
    jitter = Decimal(rng.randint(85, 130)) / 100
    declared = (income * jitter).quantize(CENT)
    account_type = "current" if segment == "self_employed" else "savings"
    return Plan(segment, income, scale, declared, account_type)


def last_working_day(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    day = nxt - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _uplift(day: date, spec: BackgroundSpec) -> int:
    for f in spec.festivals:
        if 0 <= (f.date - day).days <= 6:
            return spec.festival_uplift_percent
    return 100


def background_v1(
    b: Builder,
    account: str,
    start: date,
    days: int,
    plan: Plan,
    pools: Pools,
    spec: BackgroundSpec,
) -> None:
    rng = b.rng
    regular = rng.sample(pools.merchants, k=min(12, len(pools.merchants)))
    friends = rng.sample(pools.peers, k=min(8, len(pools.peers)))
    employer = rng.choice(pools.employers)
    pays_month_end = rng.random() < 0.5
    landlord = b.ids.next("CPTY", 6)
    lender = rng.choice(pools.suppliers)
    has_emi = rng.randrange(100) < spec.percent("emi_percent")
    emi = paise(rng, *spec.span("emi"))
    rent_pct = rng.randint(*spec.span("rent_share"))
    gets_bonus = rng.randrange(100) < spec.percent("festival_bonus_percent")
    family = rng.choice(pools.peers)
    suppliers = rng.sample(pools.suppliers, k=min(4, len(pools.suppliers)))
    counts = spec.monthly[plan.segment]

    def add(
        day: date,
        amount: Decimal,
        direction: str,
        channel: Channel,
        cpty: str | None,
        memo: str | None,
        hour: tuple[int, int] = (8, 22),
    ) -> None:
        ts = _local(day, rng.randint(*hour), rng.randint(0, 59))
        b.txn(account, ts, amount, direction, channel, cpty, memo)  # type: ignore[arg-type]

    for offset in range(days):
        day = start + timedelta(days=offset)
        month_end = last_working_day(day.year, day.month)

        # Income on its usual day.
        if plan.segment == "salaried":
            pay_day = month_end if pays_month_end else date(day.year, day.month, 1)
            if day == pay_day:
                add(day, plan.monthly_income, "credit", "NEFT", employer, "salary", (9, 12))
            if gets_bonus and day == spec_diwali_eve(spec, day):
                add(
                    day,
                    paise(rng, *spec.span("festival_bonus")),
                    "credit",
                    "NEFT",
                    employer,
                    "festival bonus",
                    (9, 12),
                )
            if day.day == 3:
                rent = (plan.monthly_income * rent_pct / 100).quantize(CENT)
                add(day, rent, "debit", rng.choice(("NEFT", "UPI")), landlord, "rent")
            if has_emi and day.day == 7:
                add(day, emi, "debit", "NEFT", lender, "loan emi", (6, 9))
        elif plan.segment == "pensioner" and day.day == 1:
            add(day, plan.monthly_income, "credit", "NEFT", employer, "pension", (9, 12))
        elif plan.segment == "student" and day.day == 2:
            add(
                day, plan.monthly_income, "credit", rng.choice(("IMPS", "UPI")), family, "allowance"
            )
        elif plan.segment == "self_employed":
            low, high = spec.span("shop_sales_per_month")
            sales = _count(rng, rng.randint(low, high), 100 if day.weekday() < 6 else 60)
            for _ in range(sales):
                add(
                    day,
                    ticket(rng, spec.tickets["shop_sale"]),
                    "credit",
                    "UPI",
                    rng.choice(pools.shoppers),
                    "shop sale",
                    (9, 21),
                )
            if day.weekday() == 5:
                add(
                    day,
                    paise(rng, *spec.span("shop_cash_deposit")),
                    "credit",
                    "CASH",
                    None,
                    None,
                    (11, 16),
                )
            if day.weekday() == 1:
                add(
                    day,
                    paise(rng, *spec.span("supplier_payment")),
                    "debit",
                    "NEFT",
                    rng.choice(suppliers),
                    "supplier",
                    (10, 17),
                )

        # Spending.
        uplift = _uplift(day, spec)
        for kind in SPEND_KINDS:
            boost = uplift if kind in ("upi_p2m", "card_pos") else 100
            for _ in range(_count(rng, counts.get(kind, 0), plan.spend_scale * boost // 100)):
                amount = ticket(rng, spec.tickets[kind])
                if kind == "atm_cash":
                    rounded = max(500, int(amount) // 500 * 500)
                    add(day, Decimal(rounded).quantize(CENT), "debit", "CASH", None, None)
                elif kind == "upi_p2m":
                    cpty = (
                        rng.choice(regular) if rng.random() < 0.7 else rng.choice(pools.merchants)
                    )
                    add(day, amount, "debit", "UPI", cpty, rng.choice(SPEND_MEMOS[kind]))
                elif kind == "card_pos":
                    add(
                        day,
                        amount,
                        "debit",
                        "CARD",
                        rng.choice(pools.merchants),
                        rng.choice(SPEND_MEMOS[kind]),
                        (10, 21),
                    )
                elif kind == "upi_p2p":
                    cpty = rng.choice(friends)
                    direction = "credit" if rng.random() < 0.35 else "debit"
                    add(day, amount, direction, "UPI", cpty, rng.choice(SPEND_MEMOS[kind]))
                else:
                    add(
                        day,
                        amount,
                        "debit",
                        "IMPS",
                        rng.choice(friends),
                        rng.choice(SPEND_MEMOS[kind]),
                    )


def spec_diwali_eve(spec: BackgroundSpec, day: date) -> date | None:
    """The working day a week before the Diwali in `day`'s year, when bonuses are paid."""
    for f in spec.festivals:
        if f.name == "Diwali" and f.date.year == day.year:
            eve = f.date - timedelta(days=7)
            while eve.weekday() >= 5:
                eve -= timedelta(days=1)
            return eve
    return None
