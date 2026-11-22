"""Write a generated dataset: ledger rows to `auditguard`, labels to `groundtruth`.

Each database is written in one transaction, after truncating the generator's tables, so a
rerun with the same seed leaves exactly the same data.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from auditguard.config import REPO_ROOT
from data.generator.model import Dataset

SCHEMA_DIR = REPO_ROOT / "infra" / "postgres" / "schema"
LEDGER_TABLES = (
    "daily_balance",
    "prior_report",
    "alert",
    "link",
    '"transaction"',
    "account",
    "entity_party",
    "customer",
)


def ledger_rows(ds: Dataset) -> dict[str, list[tuple[Any, ...]]]:
    """Row tuples per ledger table, in insert order (parents before children)."""
    return {
        "customer": [
            (
                c.customer_token,
                c.segment,
                c.kyc_risk_band,
                c.declared_monthly_turnover,
                c.onboarded_at,
            )
            for c in ds.customers
        ],
        "entity_party": [
            (p.party_token, p.customer_token, p.display_name, p.role) for p in ds.parties
        ],
        "account": [
            (a.account_token, a.customer_token, a.type, a.opened_at, a.status) for a in ds.accounts
        ],
        '"transaction"': [
            (
                r.txn.txn_id,
                r.txn.account_token,
                r.txn.ts,
                r.txn.amount,
                r.txn.currency,
                r.txn.direction,
                r.txn.channel,
                r.txn.counterparty_token,
                r.txn.memo_ref,
                r.memo,
                r.txn.reverses_txn_id,
            )
            for r in ds.transactions
        ],
        "daily_balance": [(b.account_token, b.local_date, b.closing_balance) for b in ds.balances],
        "link": [(x.account_token, x.linked_account_token, x.link_type) for x in ds.links],
        "alert": [
            (a.alert_id, a.account_ref, a.scenario_code, a.detected_at, a.profile)
            for a in ds.alerts
        ],
        "prior_report": [
            (r.report_id, r.subject_token, r.filed_at, r.type, r.amount, r.ack_id)
            for r in ds.prior_reports
        ],
    }


def label_rows(ds: Dataset) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "case_labels": [
            (
                lb.alert_id,
                lb.account_token,
                lb.scenario_family,
                lb.variant,
                lb.expected_disposition,
                lb.planted_txn_ids,
                lb.seed,
                lb.spec_sha256,
            )
            for lb in ds.labels
        ],
        "scenario_params": [
            (sp.alert_id, json.dumps(sp.params, sort_keys=True)) for sp in ds.scenario_params
        ],
        "data_defects (account_token, kind, txn_ids, detail)": [
            (d.account_token, d.kind, d.txn_ids, json.dumps(d.detail, sort_keys=True))
            for d in ds.defects
        ],
    }


def _insert(cur: psycopg.Cursor[Any], table: str, rows: Sequence[tuple[Any, ...]]) -> None:
    if not rows:
        return
    if table == "ledger.daily_balance":  # ~150k rows in `full`: COPY, not one INSERT each
        with cur.copy("COPY ledger.daily_balance FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
        return
    placeholders = ", ".join(["%s"] * len(rows[0]))
    cur.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)  # noqa: S608 - fixed table names


def _apply(conn: psycopg.Connection[Any], sql_file: Path) -> None:
    conn.execute(sql_file.read_text(encoding="utf-8"))


def grant_read_only(conn: psycopg.Connection[Any], ro_user: str) -> bool:
    """SELECT on ledger.* for the MCP servers' role (docs/05). False if the role is missing."""
    if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ro_user,)).fetchone() is None:
        return False
    role = sql.Identifier(ro_user)
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA ledger TO {}").format(role))
    conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA ledger TO {}").format(role))
    return True


def write_ledger(ds: Dataset, app_conninfo: str, *, ro_user: str | None = None) -> bool:
    """Returns whether the read-only role was granted access (False if it does not exist)."""
    granted = False
    with psycopg.connect(app_conninfo) as conn:
        _apply(conn, SCHEMA_DIR / "auditguard_ledger.sql")
        if ro_user:
            granted = grant_read_only(conn, ro_user)
        with conn.cursor() as cur:
            cur.execute("TRUNCATE " + ", ".join(f"ledger.{t}" for t in LEDGER_TABLES))
            for table, rows in ledger_rows(ds).items():
                _insert(cur, f"ledger.{table}", rows)
    return granted


def write_labels(ds: Dataset, groundtruth_conninfo: str) -> None:
    with psycopg.connect(groundtruth_conninfo) as conn:
        _apply(conn, SCHEMA_DIR / "groundtruth.sql")
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE groundtruth.data_defects, groundtruth.scenario_params,"
                " groundtruth.case_labels"
            )
            for table, rows in label_rows(ds).items():
                _insert(cur, f"groundtruth.{table}", rows)
