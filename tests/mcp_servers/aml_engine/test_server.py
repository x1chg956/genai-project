"""aml_engine server: wiring around the pure rule functions (which have their own tests)."""

from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastmcp import FastMCP

from auditguard.config import REPO_ROOT
from auditguard.ledger_ref import make_ledger_ref
from auditguard.schemas import RuleResult
from data.generator.generate import generate
from data.generator.spec import load_spec
from mcp_servers.aml_engine.engine.registry import load_rule_pack
from mcp_servers.aml_engine.server import build_server
from mcp_servers.common.ledger import InMemoryLedgerRepo, LedgerRepo, local_day_bounds
from tests.mcp_servers.helpers import (
    ACCT,
    CASE,
    OTHER_ACCT,
    Called,
    call,
    guard,
    small_ledger,
    small_repo,
    token,
    txn,
)

IST = ZoneInfo("Asia/Kolkata")
PACK = load_rule_pack(REPO_ROOT / "rules" / "IN", "1.0.0")
MARCH = (date(2026, 3, 1), date(2026, 3, 31))


def server(repo: LedgerRepo | None = None) -> FastMCP:
    return build_server(repo or small_repo(), PACK, guard("aml_engine"), tz=IST)


def ref_for(repo: LedgerRepo, account: str = ACCT, span: tuple[date, date] = MARCH) -> str:
    rows = repo.transactions(account, *local_day_bounds(*span, IST))
    return str(make_ledger_ref(account, *span, rows))


def run(
    tool: str, repo: LedgerRepo | None = None, agent: str = "single_agent", **args: Any
) -> Called:
    return call(server(repo), tool, {"agent_token": token(agent), "case_id": CASE} | args)


@pytest.mark.tool("evaluate_rules")
def test_evaluate_rules_happy_matches_pure_functions() -> None:
    """On generated data, the tool returns exactly what the rule functions return."""
    ds = generate(load_spec(REPO_ROOT / "data" / "generator" / "typology_spec.yaml"), seed=42)
    repo = InMemoryLedgerRepo(alerts=ds.alerts, transactions=[r.txn for r in ds.transactions])
    for alert in ds.alerts:
        span = (date(2026, 1, 1), date(2026, 3, 31))
        ref = ref_for(repo, alert.account_ref, span)
        r = call(
            server(repo),
            "evaluate_rules",
            {
                "agent_token": token("single_agent", alert.alert_id),
                "case_id": alert.alert_id,
                "ledger_ref": ref,
            },
        )
        assert not r.is_error, r.result
        rows = repo.transactions(alert.account_ref, *local_day_bounds(*span, IST))
        expected = PACK.evaluate(rows, None, IST)
        assert [RuleResult.model_validate(x) for x in r.result["results"]] == expected
        assert r.result["rule_pack_version"] == "1.0.0"
        assert r.result["txn_count"] == len(rows)


@pytest.mark.tool("evaluate_rules")
def test_evaluate_rules_subset_and_decimal_strings() -> None:
    repo = small_repo()
    r = run("evaluate_rules", repo, ledger_ref=ref_for(repo), rule_ids=["R07"])
    assert [x["rule_id"] for x in r.result["results"]] == ["R07"]
    res = r.result["results"][0]
    assert all(isinstance(v, str) for v in res["inputs"].values())
    assert isinstance(res["score"], str)


@pytest.mark.tool("evaluate_rules")
@pytest.mark.parametrize(
    "ledger_ref",
    ["nonsense", "ledger:v1:ACCT-SYN-000001:2026-03-31:2026-03-01:0123456789abcdef"],
)
def test_evaluate_rules_invalid_input(ledger_ref: str) -> None:
    r = run("evaluate_rules", ledger_ref=ledger_ref)
    assert r.is_error and r.code == "INVALID_INPUT"


@pytest.mark.tool("evaluate_rules")
def test_evaluate_rules_unknown_rule_and_profile() -> None:
    repo = small_repo()
    assert run("evaluate_rules", repo, ledger_ref=ref_for(repo), rule_ids=["R99"]).code == (
        "INVALID_INPUT"
    )
    assert run("evaluate_rules", repo, ledger_ref=ref_for(repo), profile="UK").code == (
        "INVALID_INPUT"
    )


@pytest.mark.tool("evaluate_rules")
def test_evaluate_rules_refuses_a_changed_ledger() -> None:
    before = small_repo()
    ref = ref_for(before)
    after = InMemoryLedgerRepo(
        alerts=[a for a in (before.alert(CASE),) if a],
        transactions=[*small_ledger(), txn(50, small_ledger()[0].ts, "1.00")],
    )
    r = run("evaluate_rules", after, ledger_ref=ref)
    assert r.code == "INVALID_INPUT" and "changed" in r.result["message"]


@pytest.mark.tool("evaluate_rules")
def test_evaluate_rules_ledger_defect_is_reported() -> None:
    dup = small_ledger()[0]
    repo = InMemoryLedgerRepo(
        alerts=[a for a in (small_repo().alert(CASE),) if a], transactions=[*small_ledger(), dup]
    )
    r = run("evaluate_rules", repo, ledger_ref=ref_for(repo))
    assert r.code == "INVALID_INPUT" and "duplicate" in r.result["message"]


@pytest.mark.tool("get_rule_metadata")
def test_get_rule_metadata_happy() -> None:
    r = run("get_rule_metadata", rule_id="R04")
    assert not r.is_error
    assert (r.result["rule_id"], r.result["source_tag"]) == ("R04", "project_heuristic")
    assert r.result["parameters"]["share_out_min"] == "0.90"
    assert r.result["interpretation"]


@pytest.mark.tool("get_rule_metadata")
def test_get_rule_metadata_invalid_input() -> None:
    assert run("get_rule_metadata", rule_id="R99").code == "NOT_FOUND"
    assert run("get_rule_metadata").code == "INVALID_INPUT"


@pytest.mark.tool("evaluate_rules")
@pytest.mark.tool("get_rule_metadata")
def test_wrong_agent_token() -> None:
    repo = small_repo()
    for agent in ("researcher", "build_payload", "submit", "ingest_validate"):
        assert run("evaluate_rules", repo, agent=agent, ledger_ref=ref_for(repo)).code == (
            "FORBIDDEN"
        )
    assert run("get_rule_metadata", agent="critic", rule_id="R01").code is None  # allowed


@pytest.mark.tool("evaluate_rules")
@pytest.mark.tool("get_rule_metadata")
def test_out_of_scope_case() -> None:
    repo = small_repo()
    r = run("evaluate_rules", repo, ledger_ref=ref_for(repo, OTHER_ACCT))
    assert r.code == "OUT_OF_SCOPE"
    r = call(
        server(repo),
        "get_rule_metadata",
        {"agent_token": token("single_agent", "ALR-SYN-0002"), "case_id": CASE, "rule_id": "R01"},
    )
    assert r.code == "OUT_OF_SCOPE"
