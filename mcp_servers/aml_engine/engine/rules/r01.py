"""R01 cash structuring: >= N cash deposits each in [L, T) within W days (docs/04 §3)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from zoneinfo import ZoneInfo

from auditguard.schemas import KycProfile, RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import (
    Field,
    Money,
    Ratio,
    RuleParams,
    best_window,
    ratio,
    validate_ledger,
)

RULE_ID = "R01"


class R01Params(RuleParams):
    threshold: Money = Field(gt=0)  # T
    lower_ratio: Ratio = Field(gt=0, lt=1)  # L = lower_ratio x T
    min_count: int = Field(ge=1)  # N
    window_days: int = Field(ge=1)  # W

    @property
    def lower_bound(self) -> Decimal:
        return (self.threshold * self.lower_ratio).quantize(Decimal("0.01"))


def evaluate(
    ledger: Sequence[Transaction], kyc: KycProfile | None, params: R01Params, tz: ZoneInfo
) -> RuleResult:
    del kyc  # not used by R01
    validate_ledger(ledger)
    low, high = params.lower_bound, params.threshold
    qualifying = [
        t
        for t in ledger
        if t.direction == "credit" and t.channel == "CASH" and low <= t.amount < high
    ]
    window = best_window(qualifying, params.window_days, tz, measure=len)
    members = window.members if window else ()
    count = len(members)
    total = sum((t.amount for t in members), Decimal("0.00"))
    return RuleResult(
        rule_id=RULE_ID,
        version=params.version,
        fired=count >= params.min_count,
        score=ratio(count, params.min_count),
        inputs={
            "qualifying_deposits_in_window": str(count),
            "window_start": window.start.isoformat() if window else "",
            "window_end": window.end.isoformat() if window else "",
            "total_in_window": str(total),
            "qualifying_deposits_in_ledger": str(len(qualifying)),
        },
        threshold={
            "threshold": str(high),
            "lower_bound": str(low),
            "min_count": str(params.min_count),
            "window_days": str(params.window_days),
        },
        supporting_txn_ids=[t.txn_id for t in members],
        explanation_template_id=params.explanation_template_id,
    )
