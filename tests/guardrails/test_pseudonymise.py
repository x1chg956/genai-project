"""Pseudonymisation before any model call (docs/08 §3)."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from auditguard.config import load_settings
from auditguard.db import conninfo
from auditguard.guardrails.factory import pseudonymiser
from auditguard.guardrails.pseudonymise import MemoryVault, PostgresVault, Pseudonymiser
from auditguard.hooks.tools import ToolSession
from tests.conftest import TEST_ENV
from tests.graph.world import World
from tests.mcp_servers.helpers import ACCT, CASE, NOW

KEY = b"test-pseudonym-key"


def test_same_value_same_token_different_values_differ() -> None:
    p = Pseudonymiser(KEY, MemoryVault())
    a = p.text("call 9876543210 or mail asha@example.com")
    b = p.text("again 9876543210")
    assert "9876543210" not in a and "asha@example.com" not in a
    token = p.token_for("mobile", "9876543210")
    assert token in a and token in b and token.startswith("PII-MOBILE-")
    assert p.token_for("mobile", "9876543211") != token
    assert Pseudonymiser(b"other-key", MemoryVault()).token_for("mobile", "9876543210") != token


def test_vault_holds_ciphertext_and_reveals_for_reviewers() -> None:
    vault = MemoryVault()
    p = Pseudonymiser(KEY, vault)
    p.text("PAN ABCDE1234F")
    token = p.token_for("pan", "ABCDE1234F")
    kind, ciphertext = vault.rows[token]
    assert kind == "pan" and b"ABCDE1234F" not in ciphertext
    assert p.reveal(token) == "ABCDE1234F"
    assert p.reveal("PII-PAN-00000000") is None


def test_ledger_data_passes_unchanged() -> None:
    p = Pseudonymiser(KEY, MemoryVault())
    result = {
        "ledger_ref": "ledger:v1:ACCT-SYN-000001:2025-12-21:2026-02-18:4af8fffecf474b82",
        "transactions": [{"txn_id": "TXN-SYN-0000042", "amount": "267548.44"}],
        "share_out": "0.7911",
        "ts": "2026-03-01T04:30:00Z",
        "txn_count": 27,
    }
    text = json.dumps(result)
    assert p.text(text) == text


def test_disabled_passes_everything() -> None:
    assert Pseudonymiser(KEY, MemoryVault(), enabled=False).text("9876543210") == "9876543210"


def test_factory_needs_the_key() -> None:
    from auditguard.config import ConfigError

    assert pseudonymiser(load_settings(environ=TEST_ENV)).enabled
    no_key = {k: v for k, v in TEST_ENV.items() if k != "PSEUDONYM_KEY"}
    with pytest.raises(ConfigError, match="PSEUDONYM_KEY"):
        pseudonymiser(load_settings(environ=no_key))


def test_agent_tool_output_is_pseudonymised(tmp_path: Path) -> None:
    world = World(tmp_path)
    p = Pseudonymiser(KEY, MemoryVault())
    servers = ("transactions", "aml_engine", "regulatory_kb")

    async def go() -> tuple[str, Any]:
        async with ToolSession(
            "single_agent",
            CASE,
            world.settings,
            world.client,
            servers,
            clock=lambda: NOW,
            shown=lambda s: p.text(s.replace("950.75", "mail asha@example.com")),
        ) as s:
            tool = next(t for t in await s.agent_tools() if t.name == "get_account_summary")
            text = await tool.ainvoke({"account_token": ACCT, "window_days": 7})
            return str(text), s.records[-1].result

    text, raw = asyncio.run(go())
    assert "asha@example.com" not in text and "PII-EMAIL-" in text  # what the model sees
    assert raw["credit_total"] == "950.75"  # code still checks the raw result


@pytest.mark.live
def test_postgres_vault_round_trip() -> None:
    settings = load_settings()
    info = conninfo(settings.databases.app, application_name="test")
    schema = f"pseudonym_test_{uuid.uuid4().hex[:8]}"
    try:
        p = Pseudonymiser(KEY, PostgresVault(info, schema))
        p.text("card 4111 1111 1111 1111")
        token = p.token_for("card", "4111 1111 1111 1111")
        assert p.reveal(token) == "4111 1111 1111 1111"
        with psycopg.connect(info) as conn:
            row = conn.execute(
                sql.SQL("SELECT ciphertext FROM {}").format(sql.Identifier(schema, "vault"))
            ).fetchone()
        assert row is not None and b"4111" not in bytes(row[0])
    finally:
        with psycopg.connect(info, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
