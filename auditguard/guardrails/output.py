"""Output guardrails v1, rule-based (docs/08 §4). Run by the output_guard node on the
latest draft before the approval gate; each check is toggled in `guardrails.output`.

- provenance: every material sentence cites at least one evidence ID, and every cited ID
  resolves to something a tool returned;
- pii: no raw identifier pattern (guardrails/pii.py) in the draft;
- neutral_wording: no accusation term from configs/output_policy.yaml;
- tipping_off: no customer-facing or tipping-off phrase from the same file.

A critical finding blocks the approval gate and returns the case to the analyst. Neutral
wording is a warning: it is shown at the gate but does not block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from auditguard.config import OutputGuardrails
from auditguard.guardrails.pii import find
from auditguard.schemas import DraftReport

Severity = Literal["critical", "warning"]


@dataclass(frozen=True)
class Finding:
    check: str
    severity: Severity
    sentence: int  # index into DraftSections.sentences(); -1 for the whole draft
    detail: str


@dataclass(frozen=True)
class OutputPolicy:
    accusation_terms: tuple[str, ...]
    tipping_off_phrases: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> OutputPolicy:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            accusation_terms=tuple(raw["accusation_terms"]),
            tipping_off_phrases=tuple(raw["tipping_off_phrases"]),
        )


def _phrase(text: str, phrases: tuple[str, ...]) -> list[str]:
    low = text.casefold()
    return [p for p in phrases if re.search(rf"\b{re.escape(p.casefold())}\b", low)]


def check_draft(
    draft: DraftReport, known: set[str], toggles: OutputGuardrails, policy: OutputPolicy
) -> list[Finding]:
    findings: list[Finding] = []
    for i, sentence in enumerate(draft.sections.sentences()):
        if toggles.provenance:
            if sentence.material and not sentence.evidence_ids:
                findings.append(
                    Finding("provenance", "critical", i, "material sentence cites nothing")
                )
            missing = sorted(set(sentence.evidence_ids) - known)
            if missing:
                findings.append(Finding("provenance", "critical", i, f"unresolved IDs {missing}"))
        if toggles.pii:
            for kind in find(sentence.text):
                findings.append(Finding("pii", "critical", i, f"raw {kind} pattern"))
        if toggles.tipping_off:
            for phrase in _phrase(sentence.text, policy.tipping_off_phrases):
                findings.append(
                    Finding("tipping_off", "critical", i, f"customer-facing: {phrase!r}")
                )
        if toggles.neutral_wording:
            for term in _phrase(sentence.text, policy.accusation_terms):
                findings.append(Finding("neutral_wording", "warning", i, f"accusation: {term!r}"))
    return findings


def blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "critical"]
