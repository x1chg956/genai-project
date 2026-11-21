"""Schema for data/generator/typology_spec.yaml (written by role 1; docs/04 §2).

Claude Code drafts this schema and one example entry only. Each entry describes one scenario
family and its variants. A variant inherits the family parameters and may override any of
them. Each range is `[low, high]`. Counts (inflow_count, sender_count, ...) are drawn once per
planted scenario; amounts and hours are drawn per transaction.

Parameters understood by each family planter (others are rejected):

rapid_pass_through
  inflow_count        number of inbound credits
  distinct_senders    how many different senders those credits come from (default: inflow_count)
  inflow_amount       INR per credit
  hold_hours          hours between a credit and the debit that forwards it
  share_out           share of each credit forwarded (the rest stays in the account)
  outflow_beneficiaries  number of distinct payees of the forwarding debits (default: 1)
  span_days           days over which the credits arrive

fan_in
  sender_count        distinct senders
  window_days         days over which their credits arrive
  inflow_amount       INR per credit
  hold_days           days before the collected balance leaves (omit to keep it)
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

Disposition = Literal["escalate_for_review", "no_action", "insufficient_data"]


def _exact(value: Any) -> Any:
    # YAML reads 0.85 as a float; str() gives its shortest exact repr, so Decimal is exact.
    return Decimal(str(value)) if isinstance(value, float | int) else value


ExactNumber = Annotated[Decimal, BeforeValidator(_exact)]


class Range(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    low: ExactNumber
    high: ExactNumber

    @model_validator(mode="before")
    @classmethod
    def _from_pair(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            if len(value) != 2:
                raise ValueError("a range is [low, high]")
            return {"low": value[0], "high": value[1]}
        return value

    @model_validator(mode="after")
    def _ordered(self) -> Range:
        if self.low > self.high:
            raise ValueError(f"range low {self.low} > high {self.high}")
        return self


FAMILY_PARAMETERS: dict[str, set[str]] = {
    "rapid_pass_through": {
        "inflow_count",
        "distinct_senders",
        "inflow_amount",
        "hold_hours",
        "share_out",
        "outflow_beneficiaries",
        "span_days",
    },
    "fan_in": {"sender_count", "window_days", "inflow_amount", "hold_days"},
}


class Variant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_disposition: Disposition
    parameters: dict[str, Range] = Field(default_factory=dict)


class TypologyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scenario_family: str
    description: str
    parameters: dict[str, Range]
    variants: dict[str, Variant]

    @model_validator(mode="after")
    def _known_family_and_parameters(self) -> TypologyEntry:
        allowed = FAMILY_PARAMETERS.get(self.scenario_family)
        if allowed is None:
            raise ValueError(
                f"no generator for scenario_family {self.scenario_family!r}; "
                f"known: {sorted(FAMILY_PARAMETERS)}"
            )
        for where, params in [("parameters", self.parameters)] + [
            (f"variants.{name}.parameters", v.parameters) for name, v in self.variants.items()
        ]:
            unknown = set(params) - allowed
            if unknown:
                raise ValueError(f"{self.scenario_family} {where}: unknown {sorted(unknown)}")
        if not self.variants:
            raise ValueError(f"{self.scenario_family}: at least one variant is required")
        return self

    def resolved(self, variant: str) -> dict[str, Range]:
        """Family parameters with the variant's overrides applied."""
        return {**self.parameters, **self.variants[variant].parameters}


class TypologySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    entries: list[TypologyEntry]

    @model_validator(mode="after")
    def _unique_families(self) -> TypologySpec:
        families = [e.scenario_family for e in self.entries]
        duplicates = {f for f in families if families.count(f) > 1}
        if duplicates:
            raise ValueError(f"scenario_family listed more than once: {sorted(duplicates)}")
        return self


def load_spec(path: Path) -> TypologySpec:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return TypologySpec.model_validate({"entries": raw})
