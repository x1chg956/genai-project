from __future__ import annotations

from pathlib import Path

import pytest

from mcp_servers.aml_engine.engine.registry import RulePackError, load_rule, load_rule_pack
from tests.rules.helpers import IST, pack, txn

ALL = ["R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08"]


def test_pack_loads_rules_01_to_08() -> None:
    loaded = pack()
    assert sorted(loaded.rules) == ALL
    assert all(r.meta.interpretation for r in loaded.rules.values())


def test_evaluate_all_returns_one_result_per_rule() -> None:
    results = pack().evaluate([txn("1500.00", "credit")], None, IST)
    assert [r.rule_id for r in results] == ALL


def test_evaluate_subset_and_unknown() -> None:
    assert [r.rule_id for r in pack().evaluate([], None, IST, ["R07"])] == ["R07"]
    with pytest.raises(RulePackError, match="unknown"):
        pack().evaluate([], None, IST, ["R99"])


def test_file_name_must_match_rule_id(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[2] / "rules" / "IN" / "R07.yaml"
    wrong = tmp_path / "R04.yaml"
    wrong.write_text(source.read_text())
    with pytest.raises(RulePackError, match="file name"):
        load_rule(wrong)


def test_empty_pack_rejected(tmp_path: Path) -> None:
    with pytest.raises(RulePackError):
        load_rule_pack(tmp_path, version="1.0.0")
