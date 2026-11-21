"""The same calls give the same results in memory, over stdio and over streamable HTTP.

Runs the real `python -m mcp_servers.<name>` entry points as subprocesses. regulatory_kb and
filing_portal need no database; transactions and aml_engine share the guard and runner and
are checked against Postgres in the live tests.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from auditguard.approval.records import ApprovalStore
from auditguard.config import REPO_ROOT, FormLimits
from mcp_servers.filing_portal.db import PortalDb
from mcp_servers.filing_portal.server import build_server as build_portal
from mcp_servers.regulatory_kb.index import Bm25Index, load_corpus
from mcp_servers.regulatory_kb.server import build_server as build_kb
from tests.conftest import TEST_ENV
from tests.mcp_servers.helpers import CASE, call, call_many, guard, token_now

CORPUS = REPO_ROOT / "tests" / "fixtures" / "passages_test.jsonl"


def env_for(tmp: Path) -> dict[str, str]:
    return {
        **os.environ,
        **TEST_ENV,
        "CORPUS_PASSAGES": str(CORPUS),
        "PORTAL_DB": str(tmp / "portal.db"),
        "APPROVAL_STORE": str(tmp / "approvals.db"),
    }


def stdio_client(module: str, tmp: Path) -> Client[Any]:
    return Client(
        StdioTransport(
            command=sys.executable, args=["-m", module], env=env_for(tmp), cwd=str(REPO_ROOT)
        )
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def http_client(module: str, tmp: Path) -> Iterator[Client[Any]]:
    port = free_port()
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, test only
        [sys.executable, "-m", module, "--transport", "http", "--port", str(port)],
        env=env_for(tmp) | {"MCP_HTTP_HOST": "127.0.0.1"},
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        else:
            pytest.fail(f"{module} did not start on port {port}")
        yield Client(f"http://127.0.0.1:{port}/mcp")
    finally:
        proc.terminate()
        proc.wait(timeout=10)


KB_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("search_passages", {"query": "suspicious transaction report", "as_of_date": "2026-06-01"}),
    ("get_passage", {"passage_id": "TEST-DOC-C:3:2026-01-01"}),
    ("get_passage", {"passage_id": "missing"}),
    ("search_passages", {"query": "", "as_of_date": "2026-06-01"}),
]


def strip_ids(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if k != "correlation_id"}


def kb_results(target: Any) -> list[tuple[bool, dict[str, Any]]]:
    base = {"agent_token": token_now("researcher"), "case_id": CASE}
    results = call_many(target, [(tool, base | args) for tool, args in KB_CALLS])
    return [(r.is_error, strip_ids(r.result)) for r in results]


def test_transport_parity_regulatory_kb(tmp_path: Path) -> None:
    in_memory = kb_results(
        build_kb(
            Bm25Index(load_corpus(CORPUS)),
            guard("regulatory_kb"),
            k_default=5,
            score_floor=Decimal("0.2"),
        )
    )
    assert kb_results(stdio_client("mcp_servers.regulatory_kb", tmp_path)) == in_memory
    with http_client("mcp_servers.regulatory_kb", tmp_path) as client:
        assert kb_results(client) == in_memory


def test_transport_parity_filing_portal(tmp_path: Path) -> None:
    payload = {"grounds_of_suspicion": "Test grounds.", "amount": "10.50"}
    args = {
        "agent_token": token_now("build_payload"),
        "case_id": CASE,
        "case_model_version": 1,
        "payload": payload,
    }
    local = build_portal(
        PortalDb(tmp_path / "local.db"),
        ApprovalStore(tmp_path / "local-approvals.db"),
        guard("filing_portal"),
        approval_key=b"k",
        limits=FormLimits(max_payload_bytes=200000, max_grounds_chars=8000),
    )
    expected = call(local, "prepare_payload", args).result
    assert (
        call(stdio_client("mcp_servers.filing_portal", tmp_path), "prepare_payload", args).result
        == expected
    )
    with http_client("mcp_servers.filing_portal", tmp_path) as client:
        assert call(client, "prepare_payload", args).result == expected
