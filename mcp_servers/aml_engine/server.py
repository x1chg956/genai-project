"""aml_engine server: evaluate_rules, get_rule_metadata (both C, compute).

Rules are the tested pure functions in engine/; the rule pack is loaded once at start-up and
its version is recorded in every result. The model never computes an indicator (rule 1).
"""

from __future__ import annotations

from typing import Literal
from zoneinfo import ZoneInfo

from fastmcp import FastMCP

from auditguard.config import Settings, resolve_path
from auditguard.db import conninfo
from auditguard.ledger_ref import LedgerRefError, ledger_digest, parse_ledger_ref
from auditguard.schemas import RuleMeta, ToolError
from mcp_servers.aml_engine.engine.common import LedgerDefectError
from mcp_servers.aml_engine.engine.registry import RulePack, load_rule_pack
from mcp_servers.aml_engine.models import RuleResults
from mcp_servers.common.errors import tool_error
from mcp_servers.common.guard import ToolGuard, guard_from_settings
from mcp_servers.common.labels import READ_ONLY
from mcp_servers.common.ledger import (
    LedgerRepo,
    PostgresLedgerRepo,
    case_accounts,
    local_day_bounds,
)


def build_server(repo: LedgerRepo, pack: RulePack, guard: ToolGuard, *, tz: ZoneInfo) -> FastMCP:
    mcp = FastMCP("aml_engine", middleware=[guard])

    @mcp.tool(tags={"compute"}, annotations=READ_ONLY)
    def evaluate_rules(
        agent_token: str,
        case_id: str,
        ledger_ref: str,
        rule_ids: list[str] | None = None,
        profile: Literal["IN"] = "IN",
    ) -> RuleResults | ToolError:
        """C. Run the rule pack (or the listed rule_ids) over the ledger slice named by
        ledger_ref (from get_transactions). Returns one RuleResult per rule with observed
        inputs, thresholds and supporting txn_ids. A fired rule is an indicator, not a
        decision."""
        tool = "evaluate_rules"
        try:
            ref = parse_ledger_ref(ledger_ref)
        except LedgerRefError as exc:
            return tool_error("INVALID_INPUT", str(exc), tool)
        alert = repo.alert(case_id)
        if alert is None:
            return tool_error("NOT_FOUND", f"no case {case_id}", tool)
        if ref.account_token not in case_accounts(alert):
            return tool_error(
                "OUT_OF_SCOPE", "ledger_ref account is not in this case's scope", tool
            )
        unknown = sorted(set(rule_ids or []) - set(pack.rules))
        if unknown:
            return tool_error("INVALID_INPUT", f"unknown rule_ids {unknown}", tool)
        rows = repo.transactions(
            ref.account_token, *local_day_bounds(ref.from_date, ref.to_date, tz)
        )
        if ledger_digest(rows) != ref.digest:
            return tool_error(
                "INVALID_INPUT", "ledger changed since ledger_ref was issued; fetch it again", tool
            )
        kyc = repo.kyc_for_account(ref.account_token)
        try:
            results = pack.evaluate(rows, kyc, tz, rule_ids)
        except LedgerDefectError as exc:
            return tool_error("INVALID_INPUT", f"ledger defect: {exc}", tool)
        return RuleResults(
            rule_pack_version=pack.version,
            ledger_ref=ledger_ref,
            txn_count=len(rows),
            results=results,
        )

    @mcp.tool(tags={"compute"}, annotations=READ_ONLY)
    def get_rule_metadata(agent_token: str, case_id: str, rule_id: str) -> RuleMeta | ToolError:
        """C. A rule's name, version, source tag, parameters, interpretation and linked
        passage ids."""
        rule = pack.rules.get(rule_id)
        if rule is None:
            return tool_error(
                "NOT_FOUND",
                f"no rule {rule_id!r}; pack has {sorted(pack.rules)}",
                "get_rule_metadata",
            )
        return rule.meta

    return mcp


def build_from_settings(settings: Settings) -> FastMCP:
    repo = PostgresLedgerRepo(
        conninfo(settings.databases.ledger_ro, application_name="mcp-aml-engine")
    )
    pack = load_rule_pack(resolve_path(settings.rules.pack), settings.rules.version)
    return build_server(
        repo, pack, guard_from_settings("aml_engine", settings), tz=ZoneInfo(settings.timezone)
    )
