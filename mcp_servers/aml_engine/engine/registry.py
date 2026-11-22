"""Rule pack loading and evaluation. Reads rule YAML once; rules themselves stay pure."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from auditguard.schemas import KycProfile, RuleMeta, RuleResult, Transaction
from mcp_servers.aml_engine.engine.common import RuleParams
from mcp_servers.aml_engine.engine.rules import r01, r02, r03, r04, r05, r06, r07, r08

Evaluate = Callable[[Sequence[Transaction], KycProfile | None, Any, ZoneInfo], RuleResult]

IMPLEMENTATIONS: dict[str, tuple[Evaluate, type[RuleParams]]] = {
    r01.RULE_ID: (r01.evaluate, r01.R01Params),
    r02.RULE_ID: (r02.evaluate, r02.R02Params),
    r03.RULE_ID: (r03.evaluate, r03.R03Params),
    r04.RULE_ID: (r04.evaluate, r04.R04Params),
    r05.RULE_ID: (r05.evaluate, r05.R05Params),
    r06.RULE_ID: (r06.evaluate, r06.R06Params),
    r07.RULE_ID: (r07.evaluate, r07.R07Params),
    r08.RULE_ID: (r08.evaluate, r08.R08Params),
}


class RulePackError(ValueError):
    """The rule pack on disk is inconsistent with the implemented rules."""


@dataclass(frozen=True)
class LoadedRule:
    meta: RuleMeta
    params: RuleParams
    evaluate: Evaluate


@dataclass(frozen=True)
class RulePack:
    version: str
    rules: dict[str, LoadedRule]

    def evaluate(
        self,
        ledger: Sequence[Transaction],
        kyc: KycProfile | None,
        tz: ZoneInfo,
        rule_ids: Iterable[str] | None = None,
    ) -> list[RuleResult]:
        selected = list(rule_ids) if rule_ids is not None else sorted(self.rules)
        unknown = [r for r in selected if r not in self.rules]
        if unknown:
            raise RulePackError(f"unknown rule ids {unknown}; pack has {sorted(self.rules)}")
        return [self.rules[r].evaluate(ledger, kyc, self.rules[r].params, tz) for r in selected]


def load_rule(path: Path) -> LoadedRule:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    params_raw = {k: str(v) for k, v in raw.get("parameters", {}).items()}
    meta = RuleMeta.model_validate({**raw, "parameters": params_raw})
    if meta.rule_id not in IMPLEMENTATIONS:
        raise RulePackError(f"{path.name}: no implementation for {meta.rule_id}")
    if path.stem != meta.rule_id:
        raise RulePackError(f"{path.name}: file name must match rule_id {meta.rule_id}")
    evaluate, params_cls = IMPLEMENTATIONS[meta.rule_id]
    params = params_cls.model_validate(
        {
            **raw.get("parameters", {}),
            "version": meta.version,
            "explanation_template_id": meta.explanation_template_id,
        }
    )
    return LoadedRule(meta=meta, params=params, evaluate=evaluate)


def load_rule_pack(directory: Path, version: str) -> RulePack:
    rules = {r.meta.rule_id: r for r in (load_rule(p) for p in sorted(directory.glob("*.yaml")))}
    if not rules:
        raise RulePackError(f"no rule YAML files in {directory}")
    return RulePack(version=version, rules=rules)
