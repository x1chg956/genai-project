"""R08 fan-out: >= R distinct beneficiaries paid from one account within W days (docs/04 §3)."""

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

RULE_ID = "R08"


class R08Params(RuleParams):
    min_beneficiaries: int = Field(ge=1)  # R
    window_days: int = Field(ge=1)  # W


def _distinct_beneficiaries(members: Sequence[Transaction]) -> int:
    return len({t.counterparty_token for t in members})


def evaluate(
    ledger: Sequence[Transaction], kyc: KycProfile | None, params: R08Params, tz: ZoneInfo
) -> RuleResult:
    del kyc  # not used by R08
    validate_ledger(ledger)
    debits = [t for t in ledger if t.direction == "debit" and t.counterparty_token]
    window = best_window(debits, params.window_days, tz, measure=_distinct_beneficiaries)
    members = window.members if window else ()
    payees = _distinct_beneficiaries(members)
    return RuleResult(
        rule_id=RULE_ID,
        version=params.version,
        fired=payees >= params.min_beneficiaries,
        score=ratio(payees, params.min_beneficiaries),
        inputs={
            "distinct_beneficiaries_in_window": str(payees),
            "debits_in_window": str(len(members)),
            "window_start": window.start.isoformat() if window else "",
            "window_end": window.end.isoformat() if window else "",
        },
        threshold={
            "min_beneficiaries": str(params.min_beneficiaries),
            "window_days": str(params.window_days),
        },
        supporting_txn_ids=[t.txn_id for t in members],
        explanation_template_id=params.explanation_template_id,
    )
