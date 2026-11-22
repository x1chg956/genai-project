"""R03 velocity spike: a 7-day count or value > k x the customer's own p90 (docs/04 §3).

The slice is cut into consecutive blocks of W local days from its first transaction's date.
Each block is compared with the p90 of every other block (interpretation in
rules/IN/R03.yaml).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TypeVar
from zoneinfo import ZoneInfo

from auditguard.schemas import KycProfile, RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import (
    Field,
    RuleParams,
    local_date,
    ratio,
    validate_ledger,
)

RULE_ID = "R03"
N = TypeVar("N", int, Decimal)
ZERO = Decimal("0.00")


class R03Params(RuleParams):
    multiple: Decimal = Field(gt=0)  # k
    window_days: int = Field(ge=1)  # W, the block length
    min_baseline_blocks: int = Field(ge=1)


@dataclass(frozen=True)
class _Block:
    start: date
    members: tuple[Transaction, ...]

    @property
    def count(self) -> int:
        return len(self.members)

    @property
    def value(self) -> Decimal:
        return sum((t.amount for t in self.members), ZERO)


def p90(values: Sequence[N]) -> N:
    """Nearest-rank 90th percentile: the smallest value with at least 90% at or below it."""
    ordered = sorted(values)
    rank = (9 * len(ordered) + 9) // 10  # ceil(0.9 * n)
    return ordered[rank - 1]


def blocks(ledger: Sequence[Transaction], days: int, tz: ZoneInfo) -> list[_Block]:
    if not ledger:
        return []
    first = min(local_date(t, tz) for t in ledger)
    last = max(local_date(t, tz) for t in ledger)
    n = (last - first).days // days + 1
    buckets: list[list[Transaction]] = [[] for _ in range(n)]
    for t in sorted(ledger, key=lambda t: (t.ts, t.txn_id)):
        buckets[(local_date(t, tz) - first).days // days].append(t)
    return [_Block(first + timedelta(days=i * days), tuple(b)) for i, b in enumerate(buckets)]


def evaluate(
    ledger: Sequence[Transaction], kyc: KycProfile | None, params: R03Params, tz: ZoneInfo
) -> RuleResult:
    del kyc  # the baseline is the customer's own history in the slice
    validate_ledger(ledger)
    all_blocks = blocks(ledger, params.window_days, tz)
    best: tuple[bool, Decimal, _Block, int, Decimal] | None = None
    if len(all_blocks) > params.min_baseline_blocks:
        for i, block in enumerate(all_blocks):
            others = all_blocks[:i] + all_blocks[i + 1 :]
            base_count = p90([b.count for b in others])
            base_value = p90([b.value for b in others])
            over = block.count > params.multiple * base_count or block.value > (
                params.multiple * base_value
            )
            score = max(
                ratio(block.count, params.multiple * base_count),
                ratio(block.value, params.multiple * base_value),
            )
            # Keep the strongest block; ties go to the earliest.
            if best is None or (over, score) > (best[0], best[1]):
                best = (over, score, block, base_count, base_value)
    fired = best[0] if best else False
    top = best[2] if best else None
    return RuleResult(
        rule_id=RULE_ID,
        version=params.version,
        fired=fired,
        score=best[1] if best else Decimal("0"),
        inputs={
            "blocks_in_slice": str(len(all_blocks)),
            "block_start": top.start.isoformat() if top else "",
            "block_count": str(top.count) if top else "0",
            "block_value": str(top.value) if top else "0.00",
            "baseline_p90_count": str(best[3]) if best else "",
            "baseline_p90_value": str(best[4]) if best else "",
        },
        threshold={
            "multiple": str(params.multiple),
            "window_days": str(params.window_days),
            "min_baseline_blocks": str(params.min_baseline_blocks),
        },
        supporting_txn_ids=[t.txn_id for t in top.members] if top and fired else [],
        explanation_template_id=params.explanation_template_id,
    )
