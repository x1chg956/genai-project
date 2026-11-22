"""Rows the generator produces. Ledger rows go to `auditguard`; labels go to `groundtruth`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from auditguard.schemas import Alert, Transaction


@dataclass(frozen=True)
class CustomerRow:
    customer_token: str
    segment: str
    kyc_risk_band: str
    declared_monthly_turnover: Decimal
    onboarded_at: datetime


@dataclass(frozen=True)
class PartyRow:
    party_token: str
    customer_token: str
    display_name: str
    role: str


@dataclass(frozen=True)
class AccountRow:
    account_token: str
    customer_token: str
    type: str
    opened_at: datetime
    status: str


@dataclass(frozen=True)
class TxnRow:
    txn: Transaction
    memo: str | None  # untrusted text; agents only ever see memo_ref


@dataclass(frozen=True)
class CaseLabel:
    """Ground truth for one planted scenario. Never reachable by agents or MCP servers."""

    alert_id: str
    account_token: str
    scenario_family: str
    variant: str
    expected_disposition: str
    planted_txn_ids: list[str]
    seed: int
    spec_sha256: str


@dataclass(frozen=True)
class ScenarioParams:
    alert_id: str
    params: dict[str, str]


@dataclass(frozen=True)
class DataDefect:
    """A planted data-quality defect (docs/04 §2). Ground truth: never reachable by agents."""

    account_token: str
    kind: str  # missing_days | double_post | reversal
    txn_ids: list[str]
    detail: dict[str, str]


@dataclass
class Dataset:
    seed: int
    profile: str
    customers: list[CustomerRow] = field(default_factory=list)
    parties: list[PartyRow] = field(default_factory=list)
    accounts: list[AccountRow] = field(default_factory=list)
    transactions: list[TxnRow] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    labels: list[CaseLabel] = field(default_factory=list)
    scenario_params: list[ScenarioParams] = field(default_factory=list)
    defects: list[DataDefect] = field(default_factory=list)
