-- Ledger tables in the auditguard database (docs/04 §1). Applied by the generator; idempotent.
-- `memo` is untrusted text (injection tests); agents only ever see memo_ref.
CREATE SCHEMA IF NOT EXISTS ledger;

CREATE TABLE IF NOT EXISTS ledger.customer (
    customer_token            TEXT PRIMARY KEY,
    segment                   TEXT NOT NULL,
    kyc_risk_band             TEXT NOT NULL CHECK (kyc_risk_band IN ('low', 'medium', 'high')),
    declared_monthly_turnover NUMERIC(18, 2) NOT NULL CHECK (declared_monthly_turnover >= 0),
    onboarded_at              TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger.entity_party (
    party_token    TEXT PRIMARY KEY,
    customer_token TEXT NOT NULL REFERENCES ledger.customer,
    display_name   TEXT NOT NULL,
    role           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger.account (
    account_token  TEXT PRIMARY KEY,
    customer_token TEXT NOT NULL REFERENCES ledger.customer,
    type           TEXT NOT NULL,
    opened_at      TIMESTAMPTZ NOT NULL,
    status         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger."transaction" (
    txn_id             TEXT PRIMARY KEY,
    account_token      TEXT NOT NULL REFERENCES ledger.account,
    ts                 TIMESTAMPTZ NOT NULL,
    amount             NUMERIC(18, 2) NOT NULL CHECK (amount > 0),
    currency           CHAR(3) NOT NULL DEFAULT 'INR',
    direction          TEXT NOT NULL CHECK (direction IN ('credit', 'debit')),
    channel            TEXT NOT NULL,
    counterparty_token TEXT,
    memo_ref           TEXT,
    memo               TEXT,
    reverses_txn_id    TEXT
);
-- Databases created before Phase 2 ingest v2: add the reversal reference in place.
ALTER TABLE ledger."transaction" ADD COLUMN IF NOT EXISTS reverses_txn_id TEXT;
CREATE INDEX IF NOT EXISTS transaction_account_ts ON ledger."transaction" (account_token, ts);

-- End-of-day closing balance per account and Asia/Kolkata local day, from core banking.
-- ingest_validate reconciles each day's movement against the posted rows to find feed gaps.
CREATE TABLE IF NOT EXISTS ledger.daily_balance (
    account_token   TEXT NOT NULL REFERENCES ledger.account,
    local_date      DATE NOT NULL,
    closing_balance NUMERIC(18, 2) NOT NULL,
    PRIMARY KEY (account_token, local_date)
);

CREATE TABLE IF NOT EXISTS ledger.link (
    account_token        TEXT NOT NULL REFERENCES ledger.account,
    linked_account_token TEXT NOT NULL,
    link_type            TEXT NOT NULL CHECK (link_type IN ('holder', 'introducer', 'phone', 'device')),
    PRIMARY KEY (account_token, linked_account_token, link_type)
);

CREATE TABLE IF NOT EXISTS ledger.alert (
    alert_id      TEXT PRIMARY KEY,
    account_token TEXT NOT NULL REFERENCES ledger.account,
    scenario_code TEXT NOT NULL,
    detected_at   TIMESTAMPTZ NOT NULL,
    profile       TEXT NOT NULL DEFAULT 'IN'
);

CREATE TABLE IF NOT EXISTS ledger.prior_report (
    report_id     TEXT PRIMARY KEY,
    subject_token TEXT NOT NULL,
    filed_at      TIMESTAMPTZ NOT NULL,
    type          TEXT NOT NULL,
    amount        NUMERIC(18, 2),
    ack_id        TEXT
);
