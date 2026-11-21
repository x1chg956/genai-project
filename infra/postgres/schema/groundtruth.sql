-- Ground truth in the separate groundtruth database (docs/04 §1). Only the evaluation
-- harness and the generator connect here; no MCP server and no agent ever does.
CREATE SCHEMA IF NOT EXISTS groundtruth;

CREATE TABLE IF NOT EXISTS groundtruth.case_labels (
    alert_id             TEXT PRIMARY KEY,
    account_token        TEXT NOT NULL,
    scenario_family      TEXT NOT NULL,
    variant              TEXT NOT NULL,
    expected_disposition TEXT NOT NULL
        CHECK (expected_disposition IN ('escalate_for_review', 'no_action', 'insufficient_data')),
    planted_txn_ids      TEXT[] NOT NULL,
    seed                 INTEGER NOT NULL,
    spec_sha256          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groundtruth.scenario_params (
    alert_id TEXT PRIMARY KEY REFERENCES groundtruth.case_labels,
    params   JSONB NOT NULL
);
