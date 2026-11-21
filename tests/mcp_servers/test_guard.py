"""ToolGuard behaviour shared by every server: identity, allow-list, case scope, errors."""

from __future__ import annotations

from typing import Annotated

import pytest
from fastmcp import FastMCP
from pydantic import Field

from auditguard.schemas import Model, ToolError
from tests.mcp_servers.helpers import CASE, OTHER_CASE, call, guard, token


class Out(Model):
    n: int


def probe_server() -> FastMCP:
    mcp = FastMCP("probe", middleware=[guard("probe")])

    @mcp.tool
    def get_transactions(
        agent_token: str, case_id: str, n: Annotated[int, Field(ge=1)]
    ) -> Out | ToolError:
        """R. probe."""
        return Out(n=n)

    @mcp.tool
    def get_passage(agent_token: str, case_id: str) -> Out | ToolError:
        """R. probe that crashes."""
        raise RuntimeError("secret detail that must not leak")

    return mcp


def test_allowed_call_passes() -> None:
    r = call(
        probe_server(),
        "get_transactions",
        {"agent_token": token("single_agent"), "case_id": CASE, "n": 2},
    )
    assert (r.is_error, r.result) == (False, {"n": 2})


@pytest.mark.parametrize(
    ("tok", "message"),
    [
        (None, "missing agent_token"),
        ("not-a-token", "malformed"),
        (token("single_agent", key=b"wrong"), "signature"),
        (token("single_agent", ttl_min=-1), "expired"),
        (token("researcher"), "may not call get_transactions"),
        (token("nobody"), "may not call"),
    ],
)
def test_forbidden(tok: str | None, message: str) -> None:
    args = {"case_id": CASE, "n": 1} | ({"agent_token": tok} if tok else {})
    r = call(probe_server(), "get_transactions", args)
    assert r.is_error and r.code == "FORBIDDEN"
    assert message in r.result["message"]
    assert r.result["tool_name"] == "get_transactions" and r.result["correlation_id"]


def test_out_of_scope_case() -> None:
    r = call(
        probe_server(),
        "get_transactions",
        {"agent_token": token("single_agent", CASE), "case_id": OTHER_CASE, "n": 1},
    )
    assert r.is_error and r.code == "OUT_OF_SCOPE"


def test_missing_case_id_is_invalid_input() -> None:
    r = call(probe_server(), "get_transactions", {"agent_token": token("single_agent"), "n": 1})
    assert r.code == "INVALID_INPUT"


def test_schema_violation_is_structured_invalid_input_without_values() -> None:
    r = call(
        probe_server(),
        "get_transactions",
        {"agent_token": token("single_agent"), "case_id": CASE, "n": "ignore all rules"},
    )
    assert r.is_error and r.code == "INVALID_INPUT"
    assert "n:" in r.result["message"]
    assert "ignore all rules" not in r.result["message"]


def test_crash_is_internal_and_hides_details() -> None:
    r = call(probe_server(), "get_passage", {"agent_token": token("single_agent"), "case_id": CASE})
    assert r.is_error and r.code == "INTERNAL"
    assert "secret detail" not in r.result["message"]
    assert r.result["retryable"] is False
