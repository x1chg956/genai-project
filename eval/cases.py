"""Evaluation case format (docs/10 §2) and a loader that never reads held-out cases.

Expected outcomes are written by role 1. A scaffold leaves them null and says so in `_todo`;
every checker skips a null expectation instead of guessing one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from auditguard.config import REPO_ROOT
from auditguard.schemas import Recommendation

HELDOUT_IN_REPO = REPO_ROOT / "eval" / "heldout"


class _Case(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ExpectedRules(_Case):
    must_fire: list[str] = Field(default_factory=list)
    must_not_fire: list[str] = Field(default_factory=list)


class ExpectedHypotheses(_Case):
    confirmed: list[str] = Field(default_factory=list)
    refuted: list[str] = Field(default_factory=list)


class ExpectedTrajectory(_Case):
    required_tool_classes: list[Literal["rules", "drilldown", "retrieval"]] = Field(
        default_factory=list
    )
    forbidden_tools: list[str] = Field(default_factory=list)
    max_tool_calls: int | None = None


class ExpectedReplan(_Case):
    required: bool = False
    reason: str | None = None


class GateScript(_Case):
    """How the unattended runner answers each interrupt (docs/10 §2)."""

    decision: Literal["file", "no_file", "needs_info"]
    reason_code: str | None = None
    rationale: str = ""
    approval: Literal["approve", "reject"] = "approve"


class EvalCase(_Case):
    case_id: str
    split: Literal["dev", "heldout"]
    scenario_family: str
    variant: str | None = None
    seed: int | None = None
    alert_id: str | None = None
    expected_rule_outputs: ExpectedRules | None = None
    expected_disposition: Recommendation | None = None
    expected_hypotheses: ExpectedHypotheses | None = None
    expected_evidence_ids: list[str] | None = None
    expected_passage_ids: list[str] | None = None
    expected_trajectory: ExpectedTrajectory | None = None
    expected_replan: ExpectedReplan | None = None
    required_facts: list[str] | None = None
    forbidden_claims: list[str] | None = None
    history_links: list[str] = Field(default_factory=list)
    attack_label: str | None = None
    gate_script: GateScript | None = None
    todo: str | None = Field(default=None, alias="_todo")


class HeldOutError(PermissionError):
    """Refused: held-out cases are never read by development tooling."""


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def check_not_heldout(path: Path, environ: dict[str, str] | None = None) -> None:
    """Refuse a path under HELDOUT_DIR or eval/heldout/ before anything opens it."""
    env = os.environ if environ is None else environ
    roots = [HELDOUT_IN_REPO] + ([Path(env["HELDOUT_DIR"])] if env.get("HELDOUT_DIR") else [])
    if any(_under(path, root) for root in roots):
        raise HeldOutError(f"{path} is held-out data; development tooling never reads it")


def load_cases(path: Path, environ: dict[str, str] | None = None) -> list[EvalCase]:
    check_not_heldout(path, environ)
    cases: list[EvalCase] = []
    with path.open(encoding="utf-8") as fh:
        for number, line in enumerate(fh, start=1):
            if line.strip():
                try:
                    cases.append(EvalCase.model_validate(json.loads(line)))
                except ValueError as exc:
                    raise ValueError(f"{path.name}:{number}: {exc}") from None
    ids = [c.case_id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path.name}: duplicate case_id")
    return cases
