"""The portal's own SQLite: drafts, submissions, receipts. No other server touches it."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from auditguard.schemas import Receipt

_SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    draft_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id        TEXT NOT NULL,
    report_version INTEGER NOT NULL,
    profile        TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    sha256         TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS drafts_case ON drafts (case_id, draft_id);
CREATE TABLE IF NOT EXISTS submissions (
    idempotency_key TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL,
    payload_sha256  TEXT NOT NULL,
    approval_id     TEXT NOT NULL,
    draft_id        INTEGER NOT NULL REFERENCES drafts,
    submitted_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
    ack_id          TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE REFERENCES submissions,
    case_id         TEXT NOT NULL,
    payload_sha256  TEXT NOT NULL,
    ts              TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Draft:
    draft_id: int
    case_id: str
    report_version: int
    canonical_json: str
    sha256: str


class PortalDb:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            conn.executescript(_SCHEMA)  # commits by itself, so not inside transaction()
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One write transaction; BEGIN IMMEDIATE takes the write lock up front."""
        conn = sqlite3.connect(self.path, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @staticmethod
    def add_draft(
        conn: sqlite3.Connection,
        *,
        case_id: str,
        report_version: int,
        profile: str,
        canonical_json: str,
        sha256: str,
        now: datetime,
    ) -> int:
        cur = conn.execute(
            "INSERT INTO drafts (case_id, report_version, profile, canonical_json, sha256,"
            " created_at) VALUES (?,?,?,?,?,?)",
            (case_id, report_version, profile, canonical_json, sha256, now.isoformat()),
        )
        assert cur.lastrowid is not None  # noqa: S101 - sqlite always sets it after INSERT
        return cur.lastrowid

    @staticmethod
    def latest_draft(conn: sqlite3.Connection, case_id: str) -> Draft | None:
        row = conn.execute(
            "SELECT draft_id, case_id, report_version, canonical_json, sha256 FROM drafts"
            " WHERE case_id = ? ORDER BY draft_id DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        return None if row is None else Draft(*row)

    @staticmethod
    def receipt(conn: sqlite3.Connection, case_id: str, idempotency_key: str) -> Receipt | None:
        row = conn.execute(
            "SELECT ack_id, case_id, payload_sha256, ts FROM receipts"
            " WHERE idempotency_key = ? AND case_id = ?",
            (idempotency_key, case_id),
        ).fetchone()
        if row is None:
            return None
        return Receipt(
            ack_id=row[0], case_id=row[1], payload_sha256=row[2], ts=datetime.fromisoformat(row[3])
        )

    @staticmethod
    def add_submission(
        conn: sqlite3.Connection,
        *,
        idempotency_key: str,
        draft: Draft,
        approval_id: str,
        receipt: Receipt,
    ) -> None:
        conn.execute(
            "INSERT INTO submissions VALUES (?,?,?,?,?,?)",
            (
                idempotency_key,
                draft.case_id,
                draft.sha256,
                approval_id,
                draft.draft_id,
                receipt.ts.isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO receipts VALUES (?,?,?,?,?)",
            (
                receipt.ack_id,
                idempotency_key,
                receipt.case_id,
                receipt.payload_sha256,
                receipt.ts.isoformat(),
            ),
        )
