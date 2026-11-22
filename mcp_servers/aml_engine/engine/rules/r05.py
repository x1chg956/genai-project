"""R05 dormant reactivation: no activity for D days, then value > V within 30 days (docs/04 §3).

V = multiple x the median transaction amount in the year before the account went quiet
(interpretation in rules/IN/R05.yaml). The slice must reach back past the dormant spell, so
this rule needs a slice longer than D days to fire.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from auditguard.schemas import KycProfile, RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import (
    Field,
    RuleParams,
    local_date,
    ratio,
    validate_ledger,
)

RULE_ID = "R05"
ZERO = Decimal("0.00")
CENT = Decimal("0.01")


class R05Params(RuleParams):
    dormant_days: int = Field(ge=1)  # D
    reactivation_days: int = Field(ge=1)  # 30
    median_multiple: Decimal = Field(gt=0)  # 5
    lookback_days: int = Field(ge=1)  # 365: "last year"


def median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return ((ordered[mid - 1] + ordered[mid]) / 2).quantize(CENT)


@dataclass(frozen=True)
class _Reactivation:
    quiet_days: int
    last_active: date
    reactivated: date
    limit: Decimal
    value: Decimal
    burst: tuple[Transaction, ...]

    @property
    def fired(self) -> bool:
        return self.value > self.limit

    @property
    def score(self) -> Decimal:
        return ratio(self.value, self.limit)


def reactivations(
    ordered: Sequence[Transaction], params: R05Params, tz: ZoneInfo
) -> list[_Reactivation]:
    found = []
    for i in range(1, len(ordered)):
        before, after = local_date(ordered[i - 1], tz), local_date(ordered[i], tz)
        quiet = (after - before).days - 1  # whole days with no transaction
        if quiet < params.dormant_days:
            continue
        since = before - timedelta(days=params.lookback_days - 1)
        history = [t.amount for t in ordered[:i] if local_date(t, tz) >= since]
        until = after + timedelta(days=params.reactivation_days - 1)
        burst = tuple(t for t in ordered[i:] if local_date(t, tz) <= until)
        found.append(
            _Reactivation(
                quiet_days=quiet,
                last_active=before,
                reactivated=after,
                limit=(median(history) * params.median_multiple).quantize(CENT),
                value=sum((t.amount for t in burst), ZERO),
                burst=burst,
            )
        )
    return found


def evaluate(
    ledger: Sequence[Transaction], kyc: KycProfile | None, params: R05Params, tz: ZoneInfo
) -> RuleResult:
    del kyc  # not used by R05
    validate_ledger(ledger)
    ordered = sorted(ledger, key=lambda t: (t.ts, t.txn_id))
    found = reactivations(ordered, params, tz)
    # The strongest reactivation; ties go to the earliest.
    best = max(found, key=lambda r: (r.fired, r.score), default=None)
    return RuleResult(
        rule_id=RULE_ID,
        version=params.version,
        fired=best.fired if best else False,
        score=best.score if best else Decimal("0"),
        inputs={
            "dormant_days": str(best.quiet_days) if best else "",
            "last_active": best.last_active.isoformat() if best else "",
            "reactivated": best.reactivated.isoformat() if best else "",
            "reactivation_value": str(best.value) if best else "0.00",
            "limit": str(best.limit) if best else "",
        },
        threshold={
            "dormant_days": str(params.dormant_days),
            "reactivation_days": str(params.reactivation_days),
            "median_multiple": str(params.median_multiple),
            "lookback_days": str(params.lookback_days),
        },
        supporting_txn_ids=[t.txn_id for t in best.burst] if best else [],
        explanation_template_id=params.explanation_template_id,
    )
