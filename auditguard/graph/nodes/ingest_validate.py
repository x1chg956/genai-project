"""ingest_validate (code): read the alerted account's window and check data quality.

Phase 1 ledger has no load calendar, so coverage is 1 when rows exist and missing days are
not detected; duplicates are counted by repeated txn_id. Generator v1 adds missing days,
duplicates and reversals in Phase 2, and this node grows with it (docs/02 Phase 2 task 1).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from auditguard.graph.deps import GraphDeps
from auditguard.graph.state import CaseState
from auditguard.hooks.tools import ToolSession, is_error
from auditguard.schemas import DataQualityReport, LedgerSlice, ToolError

CALLER = "ingest_validate"


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
        txn_ids: list[str] = []
        ref = ""
        async with session:
            page = 1
            while True:
                result = await session.call(
                    "get_transactions",
                    account_token=alert.account_ref,
                    from_date=from_date.isoformat(),
                    to_date=to_date.isoformat(),
                    page=page,
                )
                if is_error(result):
                    return {"status": "ERROR", "errors": [ToolError.model_validate(result)]}
                ref = result["ledger_ref"]
                txn_ids += [t["txn_id"] for t in result["transactions"]]
                if not result["has_more"]:
                    break
                page += 1
        duplicates = len(txn_ids) - len(set(txn_ids))
        verdict = "unusable" if not txn_ids else ("gap" if duplicates else "ok")
        quality = DataQualityReport(
            coverage_ratio=Decimal(1 if txn_ids else 0),
            duplicates_quarantined=duplicates,
            verdict=verdict,
        )
        ledger = LedgerSlice(
            account_tokens=[alert.account_ref],
            from_date=from_date,
            to_date=to_date,
            txn_count=len(set(txn_ids)),
            ref=ref,
        )
        return {"ledger": ledger, "data_quality": quality, "status": "IN_PROGRESS"}

    return ingest_validate
