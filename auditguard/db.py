"""Postgres connection strings built from config. Passwords never appear in logs or errors."""

from __future__ import annotations

from psycopg.conninfo import make_conninfo

from auditguard.config import DatabaseSettings


def conninfo(db: DatabaseSettings, *, application_name: str = "auditguard") -> str:
    return make_conninfo(
        host=db.host,
        port=db.port,
        dbname=db.name,
        user=db.user,
        password=db.password.get_secret_value() if db.password else None,
        application_name=application_name,
        connect_timeout=10,
    )


def describe(db: DatabaseSettings) -> str:
    """Safe description for messages: no password."""
    return f"{db.user}@{db.host}:{db.port}/{db.name}"
