"""post_tool (docs/08 §5): one audit event per tool call, metrics, and the audit_log sink.

The logged copy never holds argument values: only their SHA-256, the latency, the result
size, the cost and the error code. Error messages are PII-scrubbed before they are logged.
`audit.backend` picks the sink: postgres (schema `audit.schema`, table audit_log), memory
(tests) or none.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Protocol

import psycopg
from psycopg import sql

from auditguard.approval.canonical import sha256_hex
from auditguard.config import Settings
from auditguard.db import conninfo
from auditguard.guardrails.pii import scrub
from auditguard.metrics import TOOL_CALLS, TOOL_ERRORS, TOOL_LATENCY


@dataclass(frozen=True)
class ToolEvent:
    ts: datetime
    case_id: str
    caller: str
    server: str
    tool: str
    args_sha256: str
    latency_ms: int
    result_bytes: int
    status: str  # ok | error | denied
    error_code: str = ""
    message: str = ""  # scrubbed
    attempt: int = 1
    by_model: bool = False
    cost_usd: str = "0"  # MCP tools cost nothing; model cost is in the budget

    def as_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["ts"] = self.ts.isoformat()
        return out


def args_hash(args: dict[str, Any]) -> str:
    """SHA-256 of the arguments as sorted JSON (model arguments may hold any JSON type)."""
    return sha256_hex(json.dumps(args, sort_keys=True, default=str).encode("utf-8"))


def make_event(
    *,
    now: datetime,
    case_id: str,
    caller: str,
    server: str,
    tool: str,
    args: dict[str, Any],
    result: dict[str, Any],
    latency_ms: int,
    attempt: int = 1,
    by_model: bool = False,
    denied: bool = False,
) -> ToolEvent:
    code = str(result.get("error_code", ""))
    status = "denied" if denied else ("error" if code else "ok")
    return ToolEvent(
        ts=now,
        case_id=case_id,
        caller=caller,
        server=server,
        tool=tool,
        args_sha256=args_hash(args),
        latency_ms=latency_ms,
        result_bytes=len(json.dumps(result, default=str).encode("utf-8")),
        status=status,
        error_code=code,
        message=scrub(str(result.get("message", "")))[:300] if code else "",
        attempt=attempt,
        by_model=by_model,
    )


def record_metrics(event: ToolEvent) -> None:
    TOOL_CALLS.labels(event.server, event.tool, event.status).inc()
    if event.error_code:
        TOOL_ERRORS.labels(event.server, event.error_code).inc()
    TOOL_LATENCY.labels(event.server, event.tool).observe(event.latency_ms / 1000)


class AuditSink(Protocol):
    def append(self, event: ToolEvent) -> None: ...


@dataclass
class MemoryAuditSink:
    events: list[ToolEvent] = field(default_factory=list)

    def append(self, event: ToolEvent) -> None:
        self.events.append(event)


class NullAuditSink:
    def append(self, event: ToolEvent) -> None:
        return None


class PostgresAuditSink:
    """Append-only audit_log in its own schema of the app database."""

    def __init__(self, conninfo: str, schema: str) -> None:
        self._conninfo = conninfo
        self._table = sql.Identifier(schema, "audit_log")
        with psycopg.connect(conninfo) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
            conn.execute(
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {} ("
                    " id BIGSERIAL PRIMARY KEY,"
                    " ts TIMESTAMPTZ NOT NULL,"
                    " case_id TEXT NOT NULL,"
                    " kind TEXT NOT NULL,"
                    " event JSONB NOT NULL)"
                ).format(self._table)
            )

    def append(self, event: ToolEvent) -> None:
        with psycopg.connect(self._conninfo) as conn:
            conn.execute(
                sql.SQL("INSERT INTO {} (ts, case_id, kind, event) VALUES (%s, %s, %s, %s)").format(
                    self._table
                ),
                (event.ts, event.case_id, "tool_call", json.dumps(event.as_json())),
            )


def audit_sink(settings: Settings) -> AuditSink:
    """The configured sink (`audit.backend`)."""
    if settings.audit.backend == "postgres":
        app = conninfo(settings.databases.app, application_name="audit")
        return PostgresAuditSink(app, settings.audit.schema_)
    if settings.audit.backend == "memory":
        return MemoryAuditSink()
    return NullAuditSink()
