"""The configured approval store: SQLite in unit tests, Postgres live (`pytest -m live`)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from auditguard.approval.records import ApprovalStore, PostgresApprovalStore
from auditguard.approval.stores import approval_store
from auditguard.config import Settings, load_settings
from auditguard.db import conninfo
from tests.approval.test_records import record
from tests.conftest import TEST_ENV


def test_factory_follows_the_backend() -> None:
    lite = load_settings(environ=TEST_ENV)
    assert isinstance(approval_store(lite), ApprovalStore)
    default = load_settings(environ={k: v for k, v in TEST_ENV.items() if k != "APPROVAL_BACKEND"})
    assert default.approval.backend == "postgres"  # the Phase 2 default


@pytest.fixture
def pg() -> Iterator[tuple[str, str]]:
    """A throwaway schema in the real app database, dropped afterwards."""
    settings: Settings = load_settings()
    info = conninfo(settings.databases.app, application_name="test")
    schema = f"approval_test_{uuid.uuid4().hex[:8]}"
    yield info, schema
    with psycopg.connect(info, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.live
def test_postgres_round_trip_void_and_read_only(pg: tuple[str, str]) -> None:
    info, schema = pg
    reader_first = PostgresApprovalStore(info, schema, read_only=True)
    assert reader_first.get("APR-1") is None  # no table yet: nothing approved

    store = PostgresApprovalStore(info, schema)
    store.put(record())
    assert store.get("APR-1") == record()
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.put(record())  # one record per approval_id
    assert store.void_case("ALR-SYN-0001") == 1
    assert store.void_case("ALR-SYN-0001") == 0
    got = store.get("APR-1")
    assert got is not None and got.void

    reader = PostgresApprovalStore(info, schema, read_only=True)
    got_ro = reader.get("APR-1")
    assert got_ro is not None and got_ro.void
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        reader.put(record(approval_id="APR-2"))
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        reader.void_case("ALR-SYN-0001")
