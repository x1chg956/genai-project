"""Hooks v1 (docs/08 §5): input-schema check, read-only retries, post-tool audit and metrics,
and the case budget seen by pre_tool."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from prometheus_client import REGISTRY
from psycopg import sql

from auditguard.config import CaseBudget, CostRates, load_settings
from auditguard.db import conninfo
from auditguard.hooks.budgets import Budget
from auditguard.hooks.post_tool import MemoryAuditSink, PostgresAuditSink, args_hash, make_event
from auditguard.hooks.tools import ToolSession
from tests.graph.world import World
from tests.mcp_servers.helpers import ACCT, CASE, NOW

SERVERS = ("transactions", "aml_engine", "regulatory_kb", "filing_portal")


def session(world: World, **kw: Any) -> ToolSession:
    async def no_sleep(_: float) -> None:
        sleeps.append(_)

    sleeps: list[float] = []
    s = ToolSession(
        "single_agent",
        CASE,
        world.settings,
        world.client,
        SERVERS,
        clock=lambda: NOW,
        sleep=no_sleep,
        **kw,
    )
    s.sleeps = sleeps  # type: ignore[attr-defined]
    return s


def flaky(fails: int, *, retryable: bool = True) -> Any:
    calls = {"n": 0}

    async def send() -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] <= fails:
            return {
                "error_code": "TIMEOUT",
                "message": "slow",
                "retryable": retryable,
                "tool_name": "x",
                "correlation_id": "c",
            }
        return {"ok": True}

    send.calls = calls  # type: ignore[attr-defined]
    return send


def counter(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_unknown_or_missing_arguments_are_refused_before_the_server(tmp_path: Path) -> None:
    world = World(tmp_path)

    async def go() -> tuple[dict[str, Any], dict[str, Any], int]:
        async with session(world) as s:
            extra = await s.call("get_account_summary", account_token=ACCT, window_days=7, x=1)
            missing = await s.call("get_account_summary", window_days=7)
            return extra, missing, len(s.records)

    extra, missing, recorded = asyncio.run(go())
    assert extra["error_code"] == missing["error_code"] == "INVALID_INPUT"
    assert "unknown ['x']" in extra["message"] and "missing ['account_token']" in missing["message"]
    assert recorded == 0


def test_read_tool_retries_twice_with_backoff_then_gives_up(tmp_path: Path) -> None:
    world = World(tmp_path)

    async def go() -> tuple[Any, ToolSession]:
        async with session(world) as s:
            send = flaky(5)
            out = await s._guarded(
                "get_account_summary",
                {"account_token": ACCT, "window_days": 7},
                send,
                by_model=False,
            )
            return (out, send.calls["n"]), s

    (out, n), s = asyncio.run(go())
    assert out["error_code"] == "TIMEOUT" and n == 3  # 1 call + 2 retries
    assert s.sleeps == [0.2, 0.4]  # type: ignore[attr-defined]
    assert len(s.records) == 1  # one logical call


def test_read_tool_recovers_on_retry(tmp_path: Path) -> None:
    world = World(tmp_path)

    async def go() -> dict[str, Any]:
        async with session(world) as s:
            return await s._guarded(
                "get_transactions",
                {"account_token": ACCT, "from_date": "2026-03-01", "to_date": "2026-03-02"},
                flaky(1),
                by_model=False,
            )

    assert asyncio.run(go()) == {"ok": True}


def test_no_retry_for_compute_tools_or_non_retryable_errors(tmp_path: Path) -> None:
    world = World(tmp_path)

    async def go() -> tuple[int, int]:
        async with session(world) as s:
            compute = flaky(5)
            await s._guarded("evaluate_rules", {"ledger_ref": "x"}, compute, by_model=False)
            final = flaky(5, retryable=False)
            await s._guarded(
                "get_account_summary",
                {"account_token": ACCT, "window_days": 7},
                final,
                by_model=False,
            )
            return compute.calls["n"], final.calls["n"]

    assert asyncio.run(go()) == (1, 1)


def test_post_tool_logs_hash_not_values_and_counts_metrics(tmp_path: Path) -> None:
    world = World(tmp_path)
    sink = MemoryAuditSink()
    before_ok = counter(
        "tool_calls_total", server="transactions", tool="get_account_summary", status="ok"
    )
    before_err = counter("tool_errors_total", server="transactions", code="OUT_OF_SCOPE")

    async def go() -> None:
        async with session(world, audit=sink) as s:
            await s.call("get_account_summary", account_token=ACCT, window_days=7)
            await s.call("get_account_summary", account_token="ACCT-SYN-999999", window_days=7)

    asyncio.run(go())
    ok, err = sink.events
    assert ok.status == "ok" and ok.server == "transactions" and ok.case_id == CASE
    assert ok.args_sha256 == args_hash({"account_token": ACCT, "window_days": 7})
    assert ACCT not in str(ok.as_json())  # the log holds a hash, never argument values
    assert ok.result_bytes > 0 and ok.latency_ms >= 0 and ok.cost_usd == "0"
    assert err.status == "error" and err.error_code == "OUT_OF_SCOPE"
    assert (
        counter("tool_calls_total", server="transactions", tool="get_account_summary", status="ok")
        == before_ok + 1
    )
    assert counter("tool_errors_total", server="transactions", code="OUT_OF_SCOPE") == (
        before_err + 1
    )


def test_error_messages_are_scrubbed_in_the_log(tmp_path: Path) -> None:
    world = World(tmp_path)
    sink = MemoryAuditSink()

    async def send() -> dict[str, Any]:
        return {
            "error_code": "INTERNAL",
            "message": "failed for asha@example.com 9876543210",
            "retryable": False,
            "tool_name": "x",
            "correlation_id": "c",
        }

    async def go() -> None:
        async with session(world, audit=sink) as s:
            await s._guarded("evaluate_rules", {"ledger_ref": "x"}, send, by_model=False)

    asyncio.run(go())
    assert sink.events[0].message == "failed for [EMAIL] [MOBILE]"


def test_token_budget_denies_further_tool_calls(tmp_path: Path) -> None:
    world = World(tmp_path)
    limits = CaseBudget(steps=10, tool_calls=10, tokens=100, seconds=60, cost_usd=Decimal("1"))
    budget = Budget(limits, CostRates(input=Decimal(0), output=Decimal(0)))
    sink = MemoryAuditSink()

    async def go() -> dict[str, Any]:
        async with session(world, budget=budget, audit=sink) as s:
            budget.charge_model(80, 20)
            return await s.call("get_account_summary", account_token=ACCT, window_days=7)

    out = asyncio.run(go())
    assert out["error_code"] == "BUDGET_EXCEEDED" and "tokens" in out["message"]
    assert sink.events[0].status == "denied"
    assert budget.tool_calls == 0


@pytest.mark.live
def test_postgres_audit_sink_appends_rows() -> None:
    """`pytest -m live`: one event lands in a throwaway audit schema of the real app database."""
    settings = load_settings()
    info = conninfo(settings.databases.app, application_name="test")
    schema = f"audit_test_{uuid.uuid4().hex[:8]}"
    try:
        sink = PostgresAuditSink(info, schema)
        event = make_event(
            now=NOW,
            case_id=CASE,
            caller="single_agent",
            server="transactions",
            tool="get_account_summary",
            args={"account_token": ACCT},
            result={"ok": True},
            latency_ms=5,
        )
        sink.append(event)
        with psycopg.connect(info) as conn:
            row = conn.execute(
                sql.SQL("SELECT case_id, kind, event FROM {}").format(
                    sql.Identifier(schema, "audit_log")
                )
            ).fetchone()
        assert row is not None
        assert row[0] == CASE and row[1] == "tool_call"
        assert row[2]["args_sha256"] == event.args_sha256 and ACCT not in str(row[2])
    finally:
        with psycopg.connect(info, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
