"""R04 rapid pass-through: >= P% of credits debited within H hours, residual near zero.

Credits are matched to later debits first-in first-out (docs/04 §3; interpretation in
rules/IN/R04.yaml).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from auditguard.schemas import KycProfile, RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import (
    Field,
    Money,
    Ratio,
    RuleParams,
    ratio,
    validate_ledger,
)

RULE_ID = "R04"
ZERO = Decimal("0.00")


@dataclass
class _OpenCredit:
    credit: Transaction
    left: Decimal


class R04Params(RuleParams):
    share_out_min: Ratio = Field(gt=0, le=1)  # P
    hold_hours_max: int = Field(ge=1)  # H
    residual_balance_max: Money = Field(ge=0)


def evaluate(
    ledger: Sequence[Transaction], kyc: KycProfile | None, params: R04Params, tz: ZoneInfo
) -> RuleResult:
    del kyc, tz  # R04 measures elapsed hours, not calendar days
    validate_ledger(ledger)
    # Same-instant credit and debit: the credit is processed first.
    ordered = sorted(ledger, key=lambda t: (t.ts, t.direction != "credit", t.txn_id))
    hold = timedelta(hours=params.hold_hours_max)

    queue: deque[_OpenCredit] = deque()
    total_in = ZERO
    passed_fast = ZERO
    supporting: dict[str, None] = {}  # insertion-ordered set
    for t in ordered:
        if t.direction == "credit":
            queue.append(_OpenCredit(t, t.amount))
            total_in += t.amount
            continue
        remaining_debit = t.amount
        while remaining_debit > 0 and queue:
            head = queue[0]
            used = min(head.left, remaining_debit)
            if t.ts - head.credit.ts <= hold:
                passed_fast += used
                supporting[head.credit.txn_id] = None
                supporting[t.txn_id] = None
            remaining_debit -= used
            head.left -= used
            if head.left == 0:
                queue.popleft()

    residual = sum((c.left for c in queue), ZERO)
    share = ratio(passed_fast, total_in)  # rounded for display only
    # Exact comparison: passed / total >= P  <=>  passed >= P x total (no rounding).
    fired = (
        total_in > 0
        and passed_fast >= params.share_out_min * total_in
        and residual <= params.residual_balance_max
    )
    return RuleResult(
        rule_id=RULE_ID,
        version=params.version,
        fired=fired,
        score=ratio(share, params.share_out_min),
        inputs={
            "total_credits": str(total_in),
            "debited_within_hold": str(passed_fast),
            "share_out": str(share),
            "residual_balance": str(residual),
            "credit_count": str(sum(1 for t in ordered if t.direction == "credit")),
        },
        threshold={
            "share_out_min": str(params.share_out_min),
            "hold_hours_max": str(params.hold_hours_max),
            "residual_balance_max": str(params.residual_balance_max),
        },
        supporting_txn_ids=list(supporting),
        explanation_template_id=params.explanation_template_id,
    )
