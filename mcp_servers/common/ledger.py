"""Read access to the ledger for the transactions and aml_engine servers.

`PostgresLedgerRepo` connects as the SELECT-only `ledger_ro` role with read-only
transactions; it has no setting that could name the groundtruth database.
`InMemoryLedgerRepo` serves tests from rows built in code.

Case scope (Phase 1): a case_id is an alert_id, and a case may read only the alerted
account. Linked accounts join the scope in Phase 2.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from auditguard.schemas import Alert, KycProfile, Transaction


class LedgerRepo(Protocol):
    def alert(self, alert_id: str) -> Alert | None: ...

    def transactions(self, account_token: str, start: datetime, end: datetime) -> list[Transaction]:
        """Rows with start <= ts < end, ordered by (ts, txn_id)."""
        ...

    def kyc_for_account(self, account_token: str) -> KycProfile | None: ...


def local_day_bounds(from_date: date, to_date: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start of from_date, start of the day after to_date) in local time, as aware datetimes."""
    start = datetime.combine(from_date, time.min, tzinfo=tz)
    end = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=tz)
    return start, end


def case_accounts(alert: Alert) -> frozenset[str]:
    """Accounts a case may read: the alerted account (linked accounts join in Phase 2)."""
    return frozenset({alert.account_ref})


class InMemoryLedgerRepo:
    def __init__(
        self,
        *,
        alerts: Iterable[Alert],
        transactions: Iterable[Transaction],
        kyc_by_account: dict[str, KycProfile] | None = None,
    ) -> None:
        self._alerts = {a.alert_id: a for a in alerts}
        self._txns = sorted(transactions, key=lambda t: (t.ts, t.txn_id))
        self._kyc = dict(kyc_by_account or {})

    def alert(self, alert_id: str) -> Alert | None:
        return self._alerts.get(alert_id)

    def transactions(self, account_token: str, start: datetime, end: datetime) -> list[Transaction]:
        return [t for t in self._txns if t.account_token == account_token and start <= t.ts < end]

    def kyc_for_account(self, account_token: str) -> KycProfile | None:
        return self._kyc.get(account_token)


class PostgresLedgerRepo:
    def __init__(self, conninfo: str) -> None:
        self._conninfo = conninfo

    def _rows(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with (
            psycopg.connect(self._conninfo, options="-c default_transaction_read_only=on") as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            cur.execute(sql, params)
            return list(cur.fetchall())

    def alert(self, alert_id: str) -> Alert | None:
        rows = self._rows(
            "SELECT alert_id, account_token AS account_ref, scenario_code, detected_at, profile"
            " FROM ledger.alert WHERE alert_id = %s",
            (alert_id,),
        )
        return Alert.model_validate(rows[0]) if rows else None

    def transactions(self, account_token: str, start: datetime, end: datetime) -> list[Transaction]:
        rows = self._rows(
            "SELECT txn_id, account_token, ts, amount, currency, direction, channel,"
            ' counterparty_token, memo_ref FROM ledger."transaction"'
            " WHERE account_token = %s AND ts >= %s AND ts < %s ORDER BY ts, txn_id",
            (account_token, start, end),
        )
        return [Transaction.model_validate(r) for r in rows]

    def kyc_for_account(self, account_token: str) -> KycProfile | None:
        rows = self._rows(
            "SELECT c.customer_token AS subject_token, c.segment, c.kyc_risk_band,"
            " c.declared_monthly_turnover, c.onboarded_at"
            " FROM ledger.account a JOIN ledger.customer c USING (customer_token)"
            " WHERE a.account_token = %s",
            (account_token,),
        )
        return KycProfile.model_validate(rows[0]) if rows else None
