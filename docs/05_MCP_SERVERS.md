# 05 MCP servers

Four FastMCP servers under `mcp_servers/`. Each is its own package with `server.py`, `models.py` (Pydantic I/O), `db.py`, and `tests/`. Run over stdio in development and streamable HTTP in Docker; test both transports. Load them into LangGraph with `langchain-mcp-adapters` (`MultiServerMCPClient`).

## Conventions

- Every tool has typed input and output models and a docstring that states R, C or W.
- Errors are **returned**, never raised:
  ```python
  class ToolError(BaseModel):
      error_code: Literal["INVALID_INPUT","NOT_FOUND","FORBIDDEN","OUT_OF_SCOPE","STALE_HASH",
                          "APPROVAL_INVALID","TIMEOUT","BUDGET_EXCEEDED","INTERNAL"]
      message: str; retryable: bool; tool_name: str; correlation_id: str
  ```
  Tool output type is `Result | ToolError`.
- Every call carries `agent_token` (bearer) and `case_id`. The server checks the token's allowed tools and that `case_id` matches the token's scope.
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

DB role: SELECT on ledger tables only. No connection string to `groundtruth` exists in this server's config.

## aml_engine (rules, compute)

| Tool | Input | Output | Label | Phase |
|---|---|---|---|---|
| evaluate_rules | case_id, ledger_ref, rule_ids (optional), profile | list[RuleResult] | C | 1 |
| get_rule_metadata | rule_id | RuleMeta | C | 1 |

Loads rule YAML at start-up, records rule-pack version in every result.

## regulatory_kb (knowledge, read, content untrusted)

| Tool | Input | Output | Label | Phase |
|---|---|---|---|---|
| search_passages | query, as_of_date, doc_filter, k | list[Passage] | R | 1 (BM25), 2 (hybrid) |
| get_passage | passage_id | Passage | R | 1 |
| search_typologies | query, k | list[TypologyPassage] | R | 2 |
| list_document_versions | doc_id | list[DocVersion] | R | 2 |

Returns `[]` with `status="no_support"` when nothing scores above `retrieval.score_floor`.

## filing_portal (action, write, own SQLite)

| Tool | Input | Output | Label | Phase |
|---|---|---|---|---|
| prepare_payload | case_id, case_model_version, profile | PayloadPreview (canonical_json, sha256, form_limit_ok) | W (draft row only) | 1 |
| submit_approved_payload | case_id, payload_sha256, approval_id, idempotency_key | Receipt or ToolError(STALE_HASH / APPROVAL_INVALID) | W | 1 |
| get_receipt | case_id, idempotency_key | Receipt or NOT_FOUND | R | 1 |

`submit_approved_payload` in one transaction: load approval record → check approver role, approver ≠ requester, not expired, HMAC valid → re-hash stored payload and compare → check idempotency key → insert submission and receipt. Only the `submit` code node holds a token for this tool.

## Tests (per server, `tests/`)

- `test_<tool>_happy` and `test_<tool>_invalid_input` for every tool.
- `test_wrong_agent_token` → FORBIDDEN; `test_out_of_scope_case` → OUT_OF_SCOPE.
- transactions: `test_groundtruth_unreachable`.
- filing_portal: `test_submit_stale_hash`, `test_submit_self_approval`, `test_submit_expired`, `test_submit_idempotent`.
- `test_transport_parity` (stdio vs HTTP give the same results).

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
