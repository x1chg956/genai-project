"""R06 profile mismatch: a calendar month's credit turnover > m x declared monthly turnover.

Months are Asia/Kolkata calendar months (docs/04 §3; interpretation in rules/IN/R06.yaml).
The declared figure comes from the customer's KYC profile; without one R06 does not fire
and says so in its inputs.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
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

RULE_ID = "R06"
ZERO = Decimal("0.00")


class R06Params(RuleParams):
    multiple: Decimal = Field(gt=0)  # m


def evaluate(
    ledger: Sequence[Transaction], kyc: KycProfile | None, params: R06Params, tz: ZoneInfo
) -> RuleResult:
    validate_ledger(ledger)
    if kyc is None:  # cannot judge a mismatch without the declared figure
        return RuleResult(
            rule_id=RULE_ID,
            version=params.version,
            fired=False,
            score=Decimal("0"),
            inputs={"kyc_profile": "missing"},
            threshold={"multiple": str(params.multiple)},
            supporting_txn_ids=[],
            explanation_template_id=params.explanation_template_id,
        )
    months: dict[str, list[Transaction]] = defaultdict(list)
    for t in ledger:
        if t.direction == "credit":
            months[local_date(t, tz).strftime("%Y-%m")].append(t)

    def total(members: list[Transaction]) -> Decimal:
        return sum((t.amount for t in members), ZERO)

    best = min(months, key=lambda m: (-total(months[m]), m), default=None)
    members = sorted(months[best], key=lambda t: (t.ts, t.txn_id)) if best else []
    turnover = total(members)
    limit = (kyc.declared_monthly_turnover * params.multiple).quantize(Decimal("0.01"))
    return RuleResult(
        rule_id=RULE_ID,
        version=params.version,
        fired=turnover > limit,
        score=ratio(turnover, limit),
        inputs={
            "month": best or "",
            "credit_turnover": str(turnover),
            "declared_monthly_turnover": str(kyc.declared_monthly_turnover),
            "limit": str(limit),
        },
        threshold={"multiple": str(params.multiple)},
        supporting_txn_ids=[t.txn_id for t in members],
        explanation_template_id=params.explanation_template_id,
    )
