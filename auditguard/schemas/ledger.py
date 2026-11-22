"""Alert, ledger and KYC schemas (docs/03 §3, docs/04 §1)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from auditguard.schemas.common import Model, Money, Ratio, UtcDatetime

Direction = Literal["credit", "debit"]
Channel = Literal["UPI", "NEFT", "IMPS", "RTGS", "CASH", "CARD", "CHEQUE"]


class Alert(Model):
    alert_id: str
    account_ref: str
    scenario_code: str
    detected_at: UtcDatetime
    profile: Literal["IN"] = "IN"


class Transaction(Model):
    txn_id: str
    account_token: str
    ts: UtcDatetime
    amount: Money = Field(gt=0)
    direction: Direction
    channel: Channel
    counterparty_token: str | None = None
    memo_ref: str | None = None
    currency: str = "INR"
    """INR only. Kept as a field so ingest can detect mixed currency as a data defect."""
    reverses_txn_id: str | None = None
    """Set on a reversal entry: the txn_id it reverses (core-banking reference, not memo)."""


class KycProfile(Model):
    subject_token: str
    segment: str
    kyc_risk_band: Literal["low", "medium", "high"]
    declared_monthly_turnover: Money
    onboarded_at: UtcDatetime


LinkType = Literal["holder", "introducer", "phone", "device"]


class LinkedAccount(Model):
    """A ledger link (docs/04 §1) seen from one account; links are read in both directions."""

    account_token: str
    linked_account_token: str
    link_type: LinkType
    holder_token: str | None = None
    """Customer holding the linked account, when it is an account of this bank."""


class PriorReport(Model):
    """A report filed earlier on a subject (records, not memory: docs/09 §1)."""

    report_id: str
    subject_token: str
    filed_at: UtcDatetime
    type: Literal["STR", "CTR"]
    amount: Money | None = None
    ack_id: str | None = None


class LedgerSlice(Model):
    """Reference to a validated slice of the ledger: a storage key, never the rows."""

    account_tokens: list[str]
    from_date: date
    to_date: date
    txn_count: int = Field(ge=0)
    ref: str


class DataQualityReport(Model):
    coverage_ratio: Ratio = Field(ge=0, le=1)
    missing_days: list[date] = Field(default_factory=list)
    duplicates_quarantined: int = Field(default=0, ge=0)
    reversals_netted: int = Field(default=0, ge=0)
    verdict: Literal["ok", "gap", "unusable"]
