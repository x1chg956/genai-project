"""ingest_validate (code): read the alerted account's window and check data quality.

The transactions server computes the quality of the whole range (mcp_servers/common/quality.py)
with the same cleaning evaluate_rules applies, so the report and the rules always agree:
- coverage = reconciled days / expected days, against the bank's closing balances; the
  unreconciled days are the missing days (rows missing or extra);
- duplicates_quarantined and reversals_netted count what the rules leave out.
Verdict: unusable when the slice has no rows or coverage is below `ingest.min_coverage`;
gap when any day does not reconcile; ok otherwise. C1 passes a gap to the agent, which sees
the report; C2 routes it to the planner (docs/03 §4).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from auditguard.graph.deps import GraphDeps
from auditguard.graph.state import CaseState
from auditguard.hooks.tools import ToolSession, is_error
from auditguard.schemas import DataQualityReport, LedgerSlice, ToolError

CALLER = "ingest_validate"
RATIO = Decimal("0.0001")


def quality_report(
    quality: dict[str, Any], txn_total: int, min_coverage: Decimal
) -> DataQualityReport:
    expected = quality["days_expected"]
    coverage = (
        (Decimal(quality["days_reconciled"]) / Decimal(expected)).quantize(
            RATIO, rounding=ROUND_HALF_EVEN
        )
        if expected
        else Decimal(0)
    )
    missing = [date.fromisoformat(d) for d in quality["unreconciled_days"]]
    if txn_total == 0 or coverage < min_coverage:
        verdict = "unusable"
    elif missing:
        verdict = "gap"
    else:
        verdict = "ok"
    return DataQualityReport(
        coverage_ratio=coverage,
        missing_days=missing,
        duplicates_quarantined=len(quality["quarantined"]),
        reversals_netted=len(quality["netted"]),
        verdict=verdict,
    )


def make_ingest_validate(deps: GraphDeps) -> Any:
    async def ingest_validate(state: CaseState) -> dict[str, Any]:
        alert = state["alert"]
        to_date = alert.detected_at.astimezone(deps.tz).date()
        from_date = to_date - timedelta(days=deps.settings.ingest.window_days - 1)
        session = ToolSession(
            CALLER,
            state["case_id"],
            deps.settings,
            deps.clients,
            ("transactions",),
            clock=deps.clock,
            audit=deps.audit,
        )
        async with session:
            result = await session.call(
                "get_transactions",
                account_token=alert.account_ref,
                from_date=from_date.isoformat(),
                to_date=to_date.isoformat(),
                page=1,
            )
        if is_error(result):
            return {"status": "ERROR", "errors": [ToolError.model_validate(result)]}
        # Quality and the reference cover the whole range, so one page is enough here.
        quality = result["quality"]
        report = quality_report(quality, result["total"], deps.settings.ingest.min_coverage)
        ledger = LedgerSlice(
            account_tokens=[alert.account_ref],
            from_date=from_date,
            to_date=to_date,
            txn_count=quality["rule_input_count"],
            ref=result["ledger_ref"],
        )
        return {"ledger": ledger, "data_quality": report, "status": "IN_PROGRESS"}

    return ingest_validate
