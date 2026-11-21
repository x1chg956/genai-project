"""Draft report and the single agent's structured result (docs/03 §2-3)."""

from __future__ import annotations

from pydantic import Field, model_validator

from auditguard.schemas.common import Model
from auditguard.schemas.evidence import Hypothesis, Passage
from auditguard.schemas.rules import RuleResult


class DraftSentence(Model):
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    material: bool = True
    """A material claim (facts, amounts, dates, grounds) must cite at least one evidence ID."""

    @model_validator(mode="after")
    def _material_needs_evidence(self) -> DraftSentence:
        if self.material and not self.evidence_ids:
            raise ValueError("a material sentence must cite at least one evidence ID")
        return self


class DraftSections(Model):
    introduction: list[DraftSentence] = Field(default_factory=list)
    body: list[DraftSentence] = Field(default_factory=list)
    grounds: list[DraftSentence] = Field(default_factory=list)
    close: list[DraftSentence] = Field(default_factory=list)

    def sentences(self) -> list[DraftSentence]:
        return [*self.introduction, *self.body, *self.grounds, *self.close]


class DraftReport(Model):
    version: int = Field(ge=1)
    sections: DraftSections

    def cited_ids(self) -> set[str]:
        return {eid for s in self.sections.sentences() for eid in s.evidence_ids}


class SingleAgentResult(Model):
    """C1 single agent output (docs/03 §2): one structured object."""

    indicators: list[RuleResult]
    hypotheses: list[Hypothesis]
    passages: list[Passage]
    draft: DraftReport
