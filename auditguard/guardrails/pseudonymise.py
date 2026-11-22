"""Pseudonymisation before any model call (docs/08 §3, layer 2).

Every text a model is shown (the case message, tool results, the answer request) passes
through `Pseudonymiser.text`, which replaces PII-looking values (guardrails/pii.py) with
deterministic tokens such as `PII-EMAIL-3f9a1c0b`. The same value always gets the same token
(HMAC-SHA256 with `PSEUDONYM_KEY`), so a model can still tell two values apart. The token →
value mapping goes to a separate vault, encrypted (Fernet with a key derived from
`PSEUDONYM_KEY`); it never enters a prompt, a trace or a log.

Ledger identifiers (ACCT-SYN-…, TXN-SYN-…) are already pseudonymous and are left alone.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import psycopg
from cryptography.fernet import Fernet
from psycopg import sql

from auditguard.guardrails.pii import PATTERNS


class Vault(Protocol):
    def put(self, token: str, kind: str, ciphertext: bytes) -> None: ...
    def get(self, token: str) -> bytes | None: ...


@dataclass
class MemoryVault:
    rows: dict[str, tuple[str, bytes]] = field(default_factory=dict)

    def put(self, token: str, kind: str, ciphertext: bytes) -> None:
        self.rows.setdefault(token, (kind, ciphertext))

    def get(self, token: str) -> bytes | None:
        row = self.rows.get(token)
        return row[1] if row else None


class PostgresVault:
    """token → encrypted value, in its own schema of the app database."""

    def __init__(self, conninfo: str, schema: str) -> None:
        self._conninfo = conninfo
        self._table = sql.Identifier(schema, "vault")
        with psycopg.connect(conninfo) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
            conn.execute(
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {} (token TEXT PRIMARY KEY, kind TEXT NOT NULL,"
                    " ciphertext BYTEA NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                ).format(self._table)
            )

    def put(self, token: str, kind: str, ciphertext: bytes) -> None:
        with psycopg.connect(self._conninfo) as conn:
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} (token, kind, ciphertext) VALUES (%s, %s, %s)"
                    " ON CONFLICT (token) DO NOTHING"
                ).format(self._table),
                (token, kind, ciphertext),
            )

    def get(self, token: str) -> bytes | None:
        with psycopg.connect(self._conninfo) as conn:
            row = conn.execute(
                sql.SQL("SELECT ciphertext FROM {} WHERE token = %s").format(self._table),
                (token,),
            ).fetchone()
        return bytes(row[0]) if row else None


class Pseudonymiser:
    def __init__(self, key: bytes, vault: Vault, *, enabled: bool = True) -> None:
        self._key = key
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"vault:" + key).digest()))
        self._vault = vault
        self.enabled = enabled
        self._seen: set[str] = set()

    def token_for(self, kind: str, value: str) -> str:
        digest = hmac.new(self._key, f"{kind}:{value}".encode(), "sha256").hexdigest()[:8]
        return f"PII-{kind.upper()}-{digest}"

    def text(self, text: str) -> str:
        """`text` with every PII-looking value replaced by its token."""
        if not self.enabled:
            return text
        for kind, pattern in PATTERNS.items():
            text = pattern.sub(self._replacer(kind), text)
        return text

    def _replacer(self, kind: str) -> Callable[[re.Match[str]], str]:
        def replace(match: re.Match[str]) -> str:
            return self._swap(kind, match.group(0))

        return replace

    def _swap(self, kind: str, value: str) -> str:
        token = self.token_for(kind, value)
        if token not in self._seen:
            self._vault.put(token, kind, self._fernet.encrypt(value.encode("utf-8")))
            self._seen.add(token)
        return token

    def reveal(self, token: str) -> str | None:
        """The original value, for an authorised reviewer only (never for a model)."""
        ciphertext = self._vault.get(token)
        return self._fernet.decrypt(ciphertext).decode("utf-8") if ciphertext else None


def identity(text: str) -> str:
    return text
