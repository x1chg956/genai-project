"""Ground truth never reaches an MCP server (CLAUDE.md hard constraint, docs/05).

Static checks run always; the live checks need a Postgres prepared by make db-bootstrap and
make seed (`pytest -m live`).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import pytest

from auditguard.config import REPO_ROOT, Settings, load_settings
from auditguard.db import conninfo

SERVERS = REPO_ROOT / "mcp_servers"
_GT_REF = re.compile(r"databases\.groundtruth|GROUNDTRUTH_|groundtruth\.", re.IGNORECASE)


def test_groundtruth_unreachable_no_server_code_names_it() -> None:
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in SERVERS.rglob("*.py")
        if _GT_REF.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_groundtruth_unreachable_servers_connect_as_read_only_role(settings: Settings) -> None:
    ro, gt, app = (
        settings.databases.ledger_ro,
        settings.databases.groundtruth,
        settings.databases.app,
    )
    assert ro.name == app.name != gt.name
    assert ro.user not in {app.user, gt.user}
    info = conninfo(ro)
    assert f"dbname={gt.name}" not in info and f"user={gt.user}" not in info


def _live_settings() -> Settings:
    if os.environ.get("AUDITGUARD_LIVE_PG") != "1":
        pytest.skip("set AUDITGUARD_LIVE_PG=1 with a bootstrapped and seeded Postgres")
    return load_settings()


@pytest.mark.live
def test_groundtruth_unreachable_live() -> None:
    s = _live_settings()
    ro = s.databases.ledger_ro
    to_gt = ro.model_copy(update={"name": s.databases.groundtruth.name})
    with pytest.raises(psycopg.OperationalError, match="permission denied"):
        psycopg.connect(conninfo(to_gt)).close()


@pytest.mark.live
def test_read_only_role_cannot_write_live() -> None:
    s = _live_settings()
    with psycopg.connect(conninfo(s.databases.ledger_ro)) as conn:
        assert conn.execute("SELECT count(*) FROM ledger.alert").fetchone() is not None
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("DELETE FROM ledger.alert")


@pytest.mark.live
def test_transactions_server_reads_seeded_ledger_live(tmp_path: Path) -> None:
    """End to end over stdio against Postgres: the seeded alert's account is readable."""
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    from auditguard.config import require_secret
    from auditguard.security.tokens import issue_token
    from mcp_servers.common.ledger import PostgresLedgerRepo
    from mcp_servers.transactions.server import build_server
    from tests.mcp_servers.helpers import call

    s = _live_settings()
    repo = PostgresLedgerRepo(conninfo(s.databases.ledger_ro))
    alert = repo.alert("ALR-SYN-0001")
    assert alert is not None
    from mcp_servers.common.guard import guard_from_settings

    server = build_server(
        repo,
        guard_from_settings("transactions", s),
        tz=ZoneInfo(s.timezone),
        max_rows=s.mcp.max_rows,
    )
    tok = issue_token(
        "single_agent",
        "ALR-SYN-0001",
        key=require_secret(s.mcp.token_key, "MCP_TOKEN_KEY"),
        ttl=timedelta(minutes=5),
        now=datetime.now(UTC),
    )
    r = call(
        server,
        "get_account_summary",
        {
            "agent_token": tok,
            "case_id": "ALR-SYN-0001",
            "account_token": alert.account_ref,
            "window_days": 60,
        },
    )
    assert not r.is_error, r.result
    assert r.result["txn_count"] > 0
