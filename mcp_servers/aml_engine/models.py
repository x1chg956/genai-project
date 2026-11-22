"""Tool outputs of the aml_engine server."""

from __future__ import annotations

from auditguard.schemas import Model, RuleResult


class RuleResults(Model):
    rule_pack_version: str
    ledger_ref: str
    txn_count: int
    """Rows the rules saw: the slice minus quarantined duplicates and netted reversals."""
    excluded_txn_count: int = 0
    results: list[RuleResult]
