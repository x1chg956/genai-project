"""R02 integrally connected cash: a calendar month's cash, each below T, sums to >= T.

Months are Asia/Kolkata calendar months (docs/04 §3; interpretation in rules/IN/R02.yaml).
Deposits and withdrawals are summed separately and never netted.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from zoneinfo import ZoneInfo

from auditguard.schemas import KycProfile, RuleResult, Transaction
from auditguard.schemas.ledger import Direction
from mcp_servers.aml_engine.engine.common import (
    Field,
    Money,
    RuleParams,
    local_date,
    ratio,
    validate_ledger,
)

RULE_ID = "R02"
ZERO = Decimal("0.00")


class R02Params(RuleParams):
    threshold: Money = Field(gt=0)  # T


def evaluate(
    ledger: Sequence[Transaction], kyc: KycProfile | None, params: R02Params, tz: ZoneInfo
) -> RuleResult:
    del kyc  # not used by R02
    validate_ledger(ledger)
    groups: dict[tuple[str, Direction], list[Transaction]] = defaultdict(list)
    for t in ledger:
        if t.channel == "CASH" and t.amount < params.threshold:
            groups[(local_date(t, tz).strftime("%Y-%m"), t.direction)].append(t)

    def total(members: list[Transaction]) -> Decimal:
        return sum((t.amount for t in members), ZERO)

    # The month and direction with the largest sum; ties go to the earliest month, credit first.
    best_key = min(groups, key=lambda k: (-total(groups[k]), k[0], k[1] != "credit"), default=None)
    members = sorted(groups[best_key], key=lambda t: (t.ts, t.txn_id)) if best_key else []
    month_total = total(members)
    return RuleResult(
        rule_id=RULE_ID,
        version=params.version,
        fired=month_total >= params.threshold,
        score=ratio(month_total, params.threshold),
        inputs={
            "month": best_key[0] if best_key else "",
            "direction": best_key[1] if best_key else "",
            "month_total": str(month_total),
            "transactions_in_month": str(len(members)),
        },
        threshold={"threshold": str(params.threshold)},
        supporting_txn_ids=[t.txn_id for t in members],
        explanation_template_id=params.explanation_template_id,
    )
