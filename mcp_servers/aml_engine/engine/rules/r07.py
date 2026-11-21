"""R07 fan-in: >= S distinct senders to one account within W days (docs/04 §3)."""

from __future__ import annotations

from collections.abc import Sequence
from zoneinfo import ZoneInfo

from auditguard.schemas import KycProfile, RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import (
    Field,
    RuleParams,
    best_window,
    ratio,
    validate_ledger,
)

RULE_ID = "R07"


class R07Params(RuleParams):
    min_senders: int = Field(ge=1)  # S
    window_days: int = Field(ge=1)  # W


def _distinct_senders(members: Sequence[Transaction]) -> int:
    return len({t.counterparty_token for t in members})


def evaluate(
    ledger: Sequence[Transaction], kyc: KycProfile | None, params: R07Params, tz: ZoneInfo
) -> RuleResult:
    del kyc  # not used by R07
    validate_ledger(ledger)
    credits = [t for t in ledger if t.direction == "credit" and t.counterparty_token]
    window = best_window(credits, params.window_days, tz, measure=_distinct_senders)
    members = window.members if window else ()
    senders = _distinct_senders(members)
    return RuleResult(
        rule_id=RULE_ID,
        version=params.version,
        fired=senders >= params.min_senders,
        score=ratio(senders, params.min_senders),
        inputs={
            "distinct_senders_in_window": str(senders),
            "credits_in_window": str(len(members)),
            "window_start": window.start.isoformat() if window else "",
            "window_end": window.end.isoformat() if window else "",
        },
        threshold={
            "min_senders": str(params.min_senders),
            "window_days": str(params.window_days),
        },
        supporting_txn_ids=[t.txn_id for t in members],
        explanation_template_id=params.explanation_template_id,
    )
