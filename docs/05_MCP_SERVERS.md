# 05 MCP servers

Four FastMCP 4 servers under `mcp_servers/`. Each is its own package with `server.py` (`build_server(...)` for tests, `build_from_settings(settings)` for `python -m mcp_servers.<name>`) and `models.py` (Pydantic I/O); shared code is in `mcp_servers/common/` (`guard.py`, `ledger.py`, `errors.py`, `runner.py`). Tests live in `tests/mcp_servers/<name>/`. Run over stdio in development and streamable HTTP in Docker (`--transport http`); test both transports. Load them into LangGraph with `langchain.mcp` (`MCPAdapter`, package `langchain[mcp]`). `langchain-mcp-adapters` is no longer maintained and needs `mcp<2`, which FastMCP 4 does not support.

## Conventions

- Every tool has typed input and output models and a docstring that states R, C or W.
- Errors are **returned**, never raised:
  ```python
  class ToolError(BaseModel):
      error_code: Literal["INVALID_INPUT","NOT_FOUND","FORBIDDEN","OUT_OF_SCOPE","STALE_HASH",
                          "APPROVAL_INVALID","TIMEOUT","BUDGET_EXCEEDED","INTERNAL"]
      message: str; retryable: bool; tool_name: str; correlation_id: str
  ```
  Tool output type is `Result | ToolError`. FastMCP puts it in `structuredContent.result`. Every ToolError result is also marked `isError`, so an agent sees a failed call.
- Every call carries `agent_token` (bearer) and `case_id` as tool arguments. One middleware (`ToolGuard`) checks them before any tool runs:
  - a missing, forged or expired token → `FORBIDDEN`;
  - a tool not in `mcp.allow_list[agent]` → `FORBIDDEN`;
  - `case_id` missing → `INVALID_INPUT`; not the token's case → `OUT_OF_SCOPE`;
  - an argument failing its schema → `INVALID_INPUT` (field names only, never values);
  - an unexpected exception → `INTERNAL` (details only in the server log, with the `correlation_id`).
- Tokens are `agt1.<claims>.<HMAC-SHA256>`, signed with `MCP_TOKEN_KEY` (`auditguard/security/tokens.py`). Claims: agent, case_id, expiry. Code nodes and hooks issue them; agents never see them. The hooks layer (`auditguard/hooks/tools.py`, `ToolSession`) adds `agent_token` and `case_id` to each call and hides both from the model's tool schema; it also offers an agent only its allow-listed tools, counts calls against `budgets.case.tool_calls`, wraps every result shown to a model in `<data source="tool">`, and records every result for the code checks.
- Case scope (Phase 1): `case_id` is the alert ID, and a case may read only the alerted account. Other accounts → `OUT_OF_SCOPE`. Linked accounts join the scope in Phase 2.
- Money is a decimal string in JSON. Timestamps are UTC ISO 8601.
- Large results are paged or aggregated. No tool returns more than 200 rows.
- Tool descriptions are static strings; their SHA-256 is pinned in `configs/base.yaml` (`mcp.description_hashes`) from Phase 6.

## transactions (records, read)

| Tool | Input | Output | Label | Phase |
|---|---|---|---|---|
| get_transactions | case_id, account_token, from_date, to_date, page | LedgerPage | R | 1 |
| get_account_summary | case_id, account_token, window_days | AccountSummary (counts, sums, p50, p90 per channel and direction) | R | 1 |
| get_counterparty_flows | case_id, account_token, window_days, top_k | list[CounterpartyFlow] | R | 1 |
| get_kyc_profile | case_id, subject_token | KycProfile | R | 2 |
| get_linked_accounts | case_id, subject_token | list[LinkedAccount] | R | 2 |
| get_peer_baseline | case_id, segment, window_days | PeerBaseline | R | 2 |
| get_prior_reports | case_id, subject_token | list[PriorReport] | R | 2 |

DB role: `auditguard_ro` (`databases.ledger_ro`).
- It has CONNECT to `auditguard` only, and `default_transaction_read_only=on`.
- SELECT on `ledger.*` is granted by `make seed`.
- No connection string to `groundtruth` exists in this server's config; aml_engine uses the same role.

Windows: `from_date`/`to_date` are Asia/Kolkata local dates, inclusive. `window_days` counts back from the alert's detection date, inclusive. p50 and p90 use the nearest-rank method, so they are always an observed amount.

`get_transactions` returns `ledger_ref = ledger:v1:<account>:<from>:<to>:<digest>`. It names the whole range, not just the page. The digest covers the rows, so `evaluate_rules` refuses a ref whose ledger changed after it was issued.

`LedgerPage.quality` (`LedgerQuality`) also covers the whole range: days expected and reconciled against `daily_balance`, unreconciled days, quarantined duplicates, netted reversal pairs, unmatched reversals and the rule-input row count (docs/04 §1a). The page lists every row on file. `get_account_summary` and `get_counterparty_flows` use the cleaned rows, as `evaluate_rules` does.

## aml_engine (rules, compute)

| Tool | Input | Output | Label | Phase |
|---|---|---|---|---|
| evaluate_rules | case_id, ledger_ref, rule_ids (optional), profile | list[RuleResult] | C | 1 |
| get_rule_metadata | rule_id | RuleMeta | C | 1 |

Loads rule YAML at start-up, records rule-pack version in every result. `evaluate_rules` reads the rows named by `ledger_ref` itself, through the read-only role; the rows never pass through the model. It leaves out quarantined duplicates and netted reversals with the same shared function (docs/04 §1a) and reports `txn_count` (rows the rules saw) and `excluded_txn_count`. `get_rule_metadata` also takes `agent_token` and `case_id`.

## regulatory_kb (knowledge, read, content untrusted)

| Tool | Input | Output | Label | Phase |
|---|---|---|---|---|
| search_passages | query, as_of_date, doc_filter, k | list[Passage] | R | 1 (BM25), 2 (hybrid) |
| get_passage | passage_id | Passage | R | 1 |
| search_typologies | query, k | list[TypologyPassage] | R | 2 |
| list_document_versions | doc_id | list[DocVersion] | R | 2 |

Returns `[]` with `status="no_support"` when nothing scores above `retrieval.score_floor`.

Phase 1 reads `corpus.passages`, a JSONL of clauses written by `scripts/ingest.py`. Each line has passage_id, doc_id, title, version, clause_ref, effective_from, superseded_on, kind and text. The version filter is applied before ranking: in force when effective_from ≤ as_of_date and superseded_on is null or later than as_of_date. Tests use a synthetic fixture corpus (`tests/fixtures/passages_test.jsonl`) that contains no regulatory text.

## filing_portal (action, write, own SQLite)

| Tool | Input | Output | Label | Phase |
|---|---|---|---|---|
| prepare_payload | case_id, case_model_version, payload, profile | PayloadPreview (canonical_json, sha256, form_limit_ok, form_limit_issues) | W (draft row only) | 1 |
| submit_approved_payload | case_id, payload_sha256, approval_id, idempotency_key | Receipt or ToolError(STALE_HASH / APPROVAL_INVALID) | W | 1 |
| get_receipt | case_id, idempotency_key | Receipt or NOT_FOUND | R | 1 |

`prepare_payload` receives the STR payload built by the `build_payload` code node. It canonicalises the payload (`auditguard/approval/canonical.py`), refuses floats, stores the result as the case's current draft and returns its hash. The portal re-hashes exactly those stored bytes at submit. Form limits are in `filing_portal.form_limits` and are placeholders until the domain reviewer confirms them.

`submit_approved_payload` runs in one SQLite transaction:
1. Load the approval record. A missing record → `APPROVAL_INVALID`.
2. Check the record. Any failure → `APPROVAL_INVALID`:
   - same case;
   - decision is approve;
   - not void;
   - Principal Officer role;
   - approver ≠ requester;
   - not expired;
   - HMAC valid.
3. Compare the record's hash with `payload_sha256`, then re-hash the current draft and compare again. Any mismatch → `STALE_HASH`.
4. Check the idempotency key. It must equal `"<case_id>:<payload_sha256>"`; a repeat returns the same receipt.
5. Insert the submission and the receipt.

Only the `submit` code node holds a token for this tool.

Approval records (Phase 1): the approval gate writes them to a SQLite store (`approval.store_path`, `auditguard/approval/records.py`), and the portal opens it read-only. Phase 2 moves the store to Postgres behind the same methods.

## Tests (per server, `tests/`)

- `test_<tool>_happy` and `test_<tool>_invalid_input` for every tool.
- `test_wrong_agent_token` → FORBIDDEN; `test_out_of_scope_case` → OUT_OF_SCOPE.
- transactions: `test_groundtruth_unreachable`.
- filing_portal: `test_submit_stale_hash`, `test_submit_self_approval`, `test_submit_expired`, `test_submit_idempotent`.
- `test_transport_parity` (in-memory, stdio and HTTP give the same results). Phase 1 runs it for regulatory_kb and filing_portal. transactions and aml_engine need Postgres, so they are covered by the live tests (`AUDITGUARD_LIVE_PG=1 pytest -m live`).

## Agent → tool allow-list (enforced by tokens and hooks)

| Caller | Tools |
|---|---|
| ingest_validate (code) | get_transactions, get_kyc_profile, get_linked_accounts |
| single_agent (C1) | all R and C tools |
| investigator | get_account_summary, get_counterparty_flows, get_peer_baseline, get_prior_reports, evaluate_rules, get_rule_metadata, search_typologies |
| researcher | search_passages, get_passage, list_document_versions |
| critic | get_passage, get_transactions, get_rule_metadata |
| build_payload (code) | prepare_payload |
| submit (code) | submit_approved_payload, get_receipt |
