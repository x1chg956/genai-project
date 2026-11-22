"""Approval records: HMAC signing, verification and the stores (docs/08 §2).

The approval gate writes records; the filing portal only reads them. From Phase 2 they live
in Postgres (`databases.app`, schema `approval.schema`); `approval.backend: sqlite` keeps the
Phase 1 SQLite file (`approval.store_path`). Both stores have the same three methods.
"""

from __future__ import annotations

import hmac
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Protocol

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from auditguard.approval.canonical import canonical_json
from auditguard.schemas import ApprovalRecord

_UNSIGNED = {"hmac", "void"}  # void changes after signing (an edit voids the approval)


def signing_bytes(record: ApprovalRecord) -> bytes:
    fields = record.model_dump(mode="python", exclude=_UNSIGNED)
    return canonical_json(fields)


def sign(record: ApprovalRecord, key: bytes) -> str:
    return hmac.new(key, signing_bytes(record), "sha256").hexdigest()


def signed(record: ApprovalRecord, key: bytes) -> ApprovalRecord:
    return record.model_copy(update={"hmac": sign(record, key)})


def hmac_valid(record: ApprovalRecord, key: bytes) -> bool:
    return hmac.compare_digest(record.hmac, sign(record, key))


def problems(record: ApprovalRecord, *, case_id: str, key: bytes, now: datetime) -> list[str]:
    """Every reason this record cannot authorise a submission; empty means it can.

    Expiry never counts as approval. The payload hash is compared separately by the caller.
    """
    found: list[str] = []
    if record.case_id != case_id:
        found.append("approval belongs to another case")
    if record.decision != "approve":
        found.append(f"decision is {record.decision!r}, not 'approve'")
    if record.void:
        found.append("approval was voided by a later edit")
    if record.role != "principal_officer":
        found.append("approver is not a Principal Officer")
    if record.approver_id == record.requester_id:
        found.append("approver is the requester (self-approval)")
    if now >= record.expires_at:
        found.append("approval has expired")
    if not hmac_valid(record, key):
        found.append("approval signature is invalid")
    return found


_SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL,
    record_json TEXT NOT NULL,
    void        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS approvals_case ON approvals (case_id);
"""


class ApprovalRepo(Protocol):
    def put(self, record: ApprovalRecord) -> None: ...
    def get(self, approval_id: str) -> ApprovalRecord | None: ...
    def void_case(self, case_id: str) -> int: ...


class ApprovalStore:
    """SQLite store of signed approval records. `read_only=True` for the portal."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.path = path
        self.read_only = read_only
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            conn = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def put(self, record: ApprovalRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO approvals (approval_id, case_id, record_json, void) VALUES (?,?,?,?)",
                (record.approval_id, record.case_id, record.model_dump_json(), int(record.void)),
            )

    def get(self, approval_id: str) -> ApprovalRecord | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT record_json, void FROM approvals WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
        except sqlite3.OperationalError:
            if self.read_only:  # store not created yet: nothing has been approved
                return None
            raise
        if row is None:
            return None
        record = ApprovalRecord.model_validate_json(row[0])
        return record.model_copy(update={"void": bool(row[1])})

    def void_case(self, case_id: str) -> int:
        """Void every approval for the case (an edit creates a new report version)."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE approvals SET void = 1 WHERE case_id = ? AND void = 0", (case_id,)
            )
            return cur.rowcount


class PostgresApprovalStore:
    """Postgres store of signed approval records, in its own schema of the app database.

    `read_only=True` (the portal) opens every connection with
    default_transaction_read_only, so it cannot write even with the app role.
    """

    def __init__(self, conninfo: str, schema: str, *, read_only: bool = False) -> None:
        self.read_only = read_only
        self._table = sql.Identifier(schema, "approvals")
        options = "-c default_transaction_read_only=on" if read_only else ""
        self._conninfo = make_conninfo(conninfo, options=options) if options else conninfo
        if not read_only:
            with psycopg.connect(self._conninfo) as conn:
                conn.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
                )
                conn.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {} ("
                        " approval_id TEXT PRIMARY KEY,"
                        " case_id TEXT NOT NULL,"
                        " record_json TEXT NOT NULL,"
                        " void BOOLEAN NOT NULL DEFAULT false,"
                        " created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                    ).format(self._table)
                )
                conn.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS approvals_case ON {} (case_id)").format(
                        self._table
                    )
                )

    def put(self, record: ApprovalRecord) -> None:
        with psycopg.connect(self._conninfo) as conn:
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} (approval_id, case_id, record_json, void) VALUES (%s,%s,%s,%s)"
                ).format(self._table),
                (record.approval_id, record.case_id, record.model_dump_json(), record.void),
            )

    def get(self, approval_id: str) -> ApprovalRecord | None:
        try:
            with psycopg.connect(self._conninfo) as conn:
                row = conn.execute(
                    sql.SQL("SELECT record_json, void FROM {} WHERE approval_id = %s").format(
                        self._table
                    ),
                    (approval_id,),
                ).fetchone()
        except psycopg.errors.UndefinedTable:
            if self.read_only:  # store not created yet: nothing has been approved
                return None
            raise
        if row is None:
            return None
        record = ApprovalRecord.model_validate_json(row[0])
        return record.model_copy(update={"void": bool(row[1])})

    def void_case(self, case_id: str) -> int:
        """Void every approval for the case (an edit creates a new report version)."""
        with psycopg.connect(self._conninfo) as conn:
            cur = conn.execute(
                sql.SQL("UPDATE {} SET void = true WHERE case_id = %s AND NOT void").format(
                    self._table
                ),
                (case_id,),
            )
            return cur.rowcount
