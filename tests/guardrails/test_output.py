"""Rule-based output guardrails (docs/08 §4)."""

from __future__ import annotations

from typing import Any

from auditguard.config import REPO_ROOT, OutputGuardrails
from auditguard.guardrails.output import OutputPolicy, blocking, check_draft
from auditguard.schemas import DraftReport

POLICY = OutputPolicy.load(REPO_ROOT / "configs" / "output_policy.yaml")
ALL_ON = OutputGuardrails(provenance=True, pii=True, neutral_wording=True, tipping_off=True)
KNOWN = {"EV-001", "R04"}


def draft(*body: dict[str, Any]) -> DraftReport:
    return DraftReport.model_validate(
        {
            "version": 1,
            "sections": {
                "introduction": [{"text": "Alert reviewed.", "material": False}],
                "body": list(body),
                "grounds": [],
                "close": [],
            },
        }
    )


def checks(d: DraftReport, toggles: OutputGuardrails = ALL_ON) -> list[tuple[str, str]]:
    return [(f.check, f.severity) for f in check_draft(d, KNOWN, toggles, POLICY)]


def test_clean_draft_passes() -> None:
    d = draft(
        {"text": "R04 indicator observed; requires review.", "evidence_ids": ["R04", "EV-001"]}
    )
    assert checks(d) == []


def test_unresolved_id_is_critical() -> None:
    d = draft({"text": "Credits of 950.75 arrived.", "evidence_ids": ["EV-999"]})
    assert checks(d) == [("provenance", "critical")]


def test_raw_identifier_is_critical() -> None:
    d = draft({"text": "Holder reachable at 9876543210.", "evidence_ids": ["EV-001"]})
    assert checks(d) == [("pii", "critical")]


def test_tipping_off_is_critical_and_case_insensitive() -> None:
    d = draft({"text": "Please NOTIFY THE CUSTOMER of the review.", "evidence_ids": ["EV-001"]})
    assert checks(d) == [("tipping_off", "critical")]


def test_accusation_is_a_warning_that_does_not_block() -> None:
    d = draft({"text": "The holder is a money mule.", "evidence_ids": ["EV-001"]})
    findings = check_draft(d, KNOWN, ALL_ON, POLICY)
    assert [(f.check, f.severity) for f in findings] == [("neutral_wording", "warning")]
    assert blocking(findings) == []


def test_whole_words_only() -> None:
    d = draft(
        {
            "text": "Criminally fast transfers? No: discriminating review.",
            "evidence_ids": ["EV-001"],
        }
    )
    assert checks(d) == []  # 'criminal' inside other words does not match


def test_each_check_can_be_turned_off() -> None:
    d = draft({"text": "Dear customer, call 9876543210.", "evidence_ids": ["EV-999"]})
    off = OutputGuardrails(provenance=False, pii=False, neutral_wording=False, tipping_off=False)
    assert checks(d, off) == []
    assert {c for c, _ in checks(d)} == {"provenance", "pii", "tipping_off"}
