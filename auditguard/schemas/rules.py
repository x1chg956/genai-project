"""Rule result and metadata schemas (docs/03 §3, docs/04 §3)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from auditguard.schemas.common import Model, Ratio

SourceTag = Literal["legal_requirement", "institutional_policy", "project_heuristic"]


class RuleResult(Model):
    """Output of one rule over one ledger. A fired rule is an indicator, never a decision.

    `inputs` holds the observed values and `threshold` the parameters used, both as strings
    (decimal strings for money), so every number a report quotes traces back to this record.
    """

    rule_id: str
    version: str
    fired: bool
    score: Ratio = Field(ge=0)
    inputs: dict[str, str]
    threshold: dict[str, str]
    supporting_txn_ids: list[str] = Field(default_factory=list)
    explanation_template_id: str


class RuleMeta(Model):
    rule_id: str
    name: str
    version: str
    source_tag: SourceTag
    effective_from: date
    parameters: dict[str, str]
    linked_passage_ids: list[str] = Field(default_factory=list)
    benign_counter_check: str | None = None
    explanation_template_id: str
    interpretation: str = ""
    """How ambiguous wording in docs/04 §3 was turned into code; reviewed by role 2."""
