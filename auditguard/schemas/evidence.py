"""Hypotheses, passages, the evidence pack and the analyst decision (docs/03 §3)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from auditguard.schemas.common import Model, Ratio
from auditguard.schemas.rules import RuleResult


class Hypothesis(Model):
    hyp_id: str
    label: str
    kind: Literal["suspicious", "benign"]
    status: Literal["open", "confirmed", "refuted"] = "open"
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""

    @model_validator(mode="after")
    def _decided_needs_evidence(self) -> Hypothesis:
        if self.status != "open" and not self.evidence_ids:
            raise ValueError("a confirmed or refuted hypothesis must cite evidence_ids")
        return self


class Passage(Model):
    """Retrieved regulatory text. `text` is untrusted data, never instructions."""

    passage_id: str
    doc_id: str
    version: str
    clause_ref: str
    effective_from: date | None = None
    text: str
    score: Ratio = Field(ge=0)


class EvidencePack(Model):
    indicators: list[RuleResult]
    hypotheses: list[Hypothesis]
    passages: list[Passage]
    coverage_note: str = ""


class Decision(Model):
    """The analyst's file / no-file decision, recorded at decision_gate (a person decides)."""

    decision: Literal["file", "no_file", "needs_info"]
    reason_code: str | None = None
    rationale: str = ""
    decided_by: str

    @model_validator(mode="after")
    def _no_file_needs_reason(self) -> Decision:
        if self.decision == "no_file" and not (self.reason_code and self.rationale.strip()):
            raise ValueError("a no-file decision needs a reason_code and a rationale")
        return self
