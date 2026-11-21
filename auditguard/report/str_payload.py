"""Map a decided case to the mock FIU-IND STR payload (docs/08 §1). Pure code, no model.

The payload never contains ground truth, risk scores or model names. Reason codes come from
fired rules through `str_report.reason_codes`; the analyst confirms them at approval. No
wall-clock time is used, so rebuilding the same version gives the same bytes and hash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from auditguard.config import ConfigError, StrReportSettings
from auditguard.schemas import Alert, Decision, DraftReport, LedgerSlice, RuleResult

SECTIONS = ("introduction", "body", "grounds", "close")


def load_reason_codes(path: Path) -> dict[str, dict[str, str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    codes = data.get("codes")
    if not isinstance(codes, dict):
        raise ConfigError(f"{path.name}: expected a 'codes' mapping")
    return {str(k): {"code": str(v["code"]), "label": str(v["label"])} for k, v in codes.items()}


def build_str_payload(
    *,
    case_id: str,
    report_version: int,
    alert: Alert,
    ledger: LedgerSlice,
    indicators: list[RuleResult],
    draft: DraftReport,
    decision: Decision,
    report: StrReportSettings,
    reason_codes: dict[str, dict[str, str]],
    tz: ZoneInfo,
) -> dict[str, Any]:
    fired = sorted((r for r in indicators if r.fired), key=lambda r: r.rule_id)
    unmapped = [r.rule_id for r in fired if r.rule_id not in reason_codes]
    if unmapped:
        raise ConfigError(f"no reason code configured for fired rule(s): {', '.join(unmapped)}")
    narrative = {
        name: [
            {"text": s.text, "evidence_ids": sorted(s.evidence_ids)}
            for s in getattr(draft.sections, name)
        ]
        for name in SECTIONS
    }
    return {
        "report_type": "STR",
        "schema_version": "1",
        "profile": alert.profile,
        "report_details": {
            "case_id": case_id,
            "alert_id": alert.alert_id,
            "report_version": report_version,
            "alert_detected_on": alert.detected_at.astimezone(tz).date().isoformat(),
            "period": {"from": ledger.from_date.isoformat(), "to": ledger.to_date.isoformat()},
        },
        "reporting_entity": report.reporting_entity.model_dump(),
        "principal_officer": report.principal_officer.model_dump(),
        "branch": report.branch.model_dump(),
        "subjects": [{"account_token": token} for token in ledger.account_tokens],
        "accounts": [
            {"account_token": token, "ledger_ref": ledger.ref} for token in ledger.account_tokens
        ],
        "reason_codes": [
            {"rule_id": r.rule_id, "rule_version": r.version, **reason_codes[r.rule_id]}
            for r in fired
        ],
        "grounds_of_suspicion": " ".join(s.text for s in draft.sections.grounds),
        "narrative": narrative,
        "action_taken": "Referred to the Principal Officer for review. No customer contact.",
        "decided_by": decision.decided_by,
    }
