"""Tool outputs of the transactions server. Money is a decimal string in JSON."""

from __future__ import annotations

from datetime import date

from pydantic import Field

from auditguard.schemas import LinkedAccount, Model, PriorReport, Transaction
from auditguard.schemas.common import Money, UtcDatetime
from auditguard.schemas.ledger import Channel, Direction
from mcp_servers.common.quality import LedgerQuality


class LedgerPage(Model):
    account_token: str
    from_date: date
    to_date: date
    page: int = Field(ge=1)
    page_size: int
    total: int = Field(ge=0)
    has_more: bool
    ledger_ref: str
    """Reference to the whole range (all pages), for evaluate_rules."""
    quality: LedgerQuality
    """Over the whole range: balance reconciliation, quarantined duplicates, netted reversals."""
    transactions: list[Transaction]
    """Every row on file, including quarantined and netted ones (see `quality`)."""


class ChannelStats(Model):
    channel: Channel
    direction: Direction
    count: int = Field(ge=1)
    total: Money
    p50: Money
    p90: Money


class AccountSummary(Model):
    account_token: str
    window_days: int
    from_date: date
    to_date: date
    txn_count: int
    credit_total: Money
    debit_total: Money
    by_channel: list[ChannelStats]


class CounterpartyFlow(Model):
    counterparty_token: str
    credit_count: int
    credit_total: Money
    debit_count: int
    debit_total: Money
    first_seen: UtcDatetime
    last_seen: UtcDatetime


class CounterpartyFlows(Model):
    account_token: str
    window_days: int
    from_date: date
    to_date: date
    counterparties_total: int
    flows: list[CounterpartyFlow]


class LinkedAccounts(Model):
    subject_token: str
    links: list[LinkedAccount]
    """Only links of the case's own accounts; a linked account with a holder_token is
    in the case's scope and can be read with the other tools."""


class PriorReports(Model):
    subject_token: str
    reports: list[PriorReport]


class PeerStat(Model):
    p50: Money
    p90: Money


class PeerCount(Model):
    p50: int
    p90: int


class PeerBaseline(Model):
    """Per-account totals over the window across a segment's peer accounts. Aggregates only:
    no account or customer token of a peer is returned."""

    segment: str
    window_days: int
    from_date: date
    to_date: date
    peer_accounts: int
    credit_total: PeerStat
    debit_total: PeerStat
    cash_total: PeerStat
    txn_count: PeerCount
