from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auditguard.config import REPO_ROOT
from data.generator.spec import TypologySpec, load_spec

SPEC_FILE = REPO_ROOT / "data" / "generator" / "typology_spec.yaml"


def entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "scenario_family": "rapid_pass_through",
        "description": "d",
        "parameters": {"inflow_count": [8, 25], "share_out": [0.85, 0.99]},
        "variants": {"standard": {"expected_disposition": "escalate_for_review"}},
    }
    return {**base, **overrides}


def test_shipped_spec_loads_with_one_example_entry() -> None:
    spec = load_spec(SPEC_FILE)
    assert [e.scenario_family for e in spec.entries] == ["rapid_pass_through"]


def test_float_ranges_become_exact_decimals() -> None:
    spec = TypologySpec.model_validate({"entries": [entry()]})
    share = spec.entries[0].parameters["share_out"]
    assert (share.low, share.high) == (Decimal("0.85"), Decimal("0.99"))


def test_variant_overrides_family_parameters() -> None:
    e = entry(
        variants={
            "slow": {
                "expected_disposition": "escalate_for_review",
                "parameters": {"inflow_count": [2, 3]},
            }
        }
    )
    resolved = TypologySpec.model_validate({"entries": [e]}).entries[0].resolved("slow")
    assert resolved["inflow_count"].high == Decimal("3")
    assert resolved["share_out"].low == Decimal("0.85")


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ({"scenario_family": "teleportation"}, "no generator"),
        ({"parameters": {"inflow_count": [8, 25], "colour": [1, 2]}}, "unknown"),
        ({"parameters": {"inflow_count": [25, 8]}}, "low"),
        ({"parameters": {"inflow_count": [1, 2, 3]}}, "low, high"),
        ({"variants": {}}, "at least one variant"),
        ({"variants": {"x": {"expected_disposition": "file_it"}}}, "expected_disposition"),
    ],
)
def test_invalid_entries_rejected(bad: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        TypologySpec.model_validate({"entries": [entry(**bad)]})


def test_duplicate_family_rejected() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        TypologySpec.model_validate({"entries": [entry(), entry()]})


def test_empty_file_is_empty_spec(tmp_path: Path) -> None:
    empty = tmp_path / "spec.yaml"
    empty.write_text("# nothing yet\n")
    assert load_spec(empty).entries == []
