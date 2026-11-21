"""Tool outputs of the aml_engine server."""

from __future__ import annotations

from auditguard.schemas import Model, RuleResult


class RuleResults(Model):
    rule_pack_version: str
    ledger_ref: str
    txn_count: int
    results: list[RuleResult]
