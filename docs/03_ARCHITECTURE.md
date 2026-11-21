# 03 Architecture

## 1. Target architecture (Stage 2), top to bottom

```
Analyst / Principal Officer
  → Chainlit UI + FastAPI (streaming, RBAC, case threads)
  → Input guardrails (schema, pseudonymise, Prompt Guard 2, Llama Guard 4, scope)
  → Supervisor + planner (LangGraph, typed CaseState, plan, replan, Postgres checkpointer)
       code: ingest_validate
       ReAct: Investigator → Regulatory Researcher (fan-out)
       code: assemble_evidence → decision_gate interrupt()
       Drafter → Evidence Critic (≤2 revisions)
  → Output guardrails
       code: build_payload (canonical JSON + SHA-256) → approval_gate interrupt() → submit (code)
  Memory: checkpointer + store (subject_history, facts, lessons)
  Hooks around every tool call: pre (identity, allow-list, scope, budget) / post (log, scrub, cost)
  MCP: transactions (R) | aml_engine (C) | regulatory_kb (R) | filing_portal (W)
  Data: Postgres auditguard | Postgres groundtruth (no MCP access) | Qdrant | portal SQLite
  Operate: traces, Prometheus + Grafana, eval harness, Reflexion runner, CI
```

Only `submit` (a code node) calls the only write tool, and only after `approval_gate`.

## 2. The three graphs (same tools, same schemas)

### C1: single agent (Phase 1–2)

```
START → ingest_validate → single_agent (ReAct, all read+compute tools)
      → decision_gate ──no-file──→ close_no_file → END
                      └─file────→ build_payload → output_guard → approval_gate
approval_gate: approve → submit → memory_update → END
               edit    → build_payload (new version, old approval void)
               reject  → END (REJECTED)
```
`single_agent` returns one `SingleAgentResult { indicators, hypotheses, passages, draft }` as structured output.

### C0: fixed workflow (Phase 3)

```
START → ingest_validate → run_all_rules (code) → retrieve_per_rule (one search call per fired rule)
      → draft_fixed (one LLM call, fixed template) → decision_gate → (same tail as C1)
```
No model chooses a tool, a hypothesis or a route.

### C2: team (Phase 4+)

```
START → plan → ingest_validate
ingest_validate: ok → investigate | gap → replan | unusable → close_insufficient
investigate: confirmed or budget spent → research | refuted and budget left → replan | retryable error → retry, else replan
replan → plan (plan_version + 1) → next step
research (Send fan-out per sub-query) → assemble_evidence → decision_gate
decision_gate: file → draft | no-file → close_no_file | needs info → pause (NEEDS_INFO)
draft → critic: pass → output_guard | fail & revisions<2 → draft | fail at 2 → return_to_analyst
output_guard: pass → build_payload | critical → return_to_analyst
build_payload → approval_gate: approve → submit | edit → critic (new version) | return → draft | reject → END | expired → END
submit: receipt → memory_update → END | portal reject → DRAFTED | uncertain → get_receipt before any retry
```

## 3. Core schemas (Pydantic v2, `auditguard/schemas/`)

All hand-offs inherit:

```python
class HandOff(BaseModel):
    case_id: str
    run_id: str
    schema_version: str = "1"
    status: Literal["ok", "partial", "error"]
    evidence_refs: list[str] = []
    error: ToolError | None = None
```

| Model | Key fields |
|---|---|
| `Alert` | alert_id, account_ref, scenario_code, detected_at (UTC), profile="IN" |
| `Transaction` | txn_id, account_token, ts (UTC), amount (Decimal), direction, channel, counterparty_token, memo_ref |
| `LedgerSlice` | account_tokens, from_date, to_date, txn_count, ref (storage key, not the rows) |
| `DataQualityReport` | coverage_ratio, missing_days, duplicates_quarantined, reversals_netted, verdict (ok, gap, unusable) |
| `RuleResult` | rule_id, version, fired, score, inputs, threshold, supporting_txn_ids, explanation_template_id |
| `Hypothesis` | hyp_id, label, kind (suspicious, benign), status (open, confirmed, refuted), evidence_ids, reason |
| `Passage` | passage_id, doc_id, version, clause_ref, effective_from, text, score |
| `Plan` | plan_version, steps[{step_id, name, depends_on, budget}], reason |
| `EvidencePack` | indicators, hypotheses, passages, coverage note |
| `Decision` | decision (file, no_file, needs_info), reason_code, rationale, decided_by |
| `DraftSentence` | text, evidence_ids (≥1 for material claims) |
| `DraftReport` | version, sections{introduction, body, grounds, close}: list[DraftSentence] |
| `CriticReport` | code_checks[{check, passed, detail}], judge{claim_support, tone}, verdict |
| `Payload` | report_version, canonical_json, sha256 |
| `ApprovalRecord` | approval_id, case_id, payload_sha256, report_version, approver_id, role, decision, expires_at, hmac |
| `Receipt` | ack_id, case_id, payload_sha256, ts |
| `ToolError` | error_code, message, retryable, tool_name, correlation_id |

## 4. CaseState (LangGraph state)

```python
class CaseState(TypedDict):
    case_id: str; run_id: str; alert: Alert
    plan: Plan | None
    ledger: LedgerSlice | None; data_quality: DataQualityReport | None
    indicators: list[RuleResult]; hypotheses: list[Hypothesis]; passages: list[Passage]
    evidence: EvidencePack | None; decision: Decision | None
    drafts: list[DraftReport]; critic: list[CriticReport]; revisions: int
    payload: Payload | None; approval: ApprovalRecord | None; receipt: Receipt | None
    budgets: Budgets            # steps, tool_calls, tokens, seconds: used and limit
    errors: list[ToolError]
    messages: Annotated[list, add_messages]   # trimmed; never the only copy of evidence
```

Rules: evidence, plan versions and approval state live in typed fields, never only in `messages`. Nodes before an `interrupt()` must be idempotent, because LangGraph re-runs the interrupted node from its start on resume.

## 5. Case lifecycle (Postgres `cases` table)

MUST states: NEW, IN_PROGRESS, NEEDS_INFO, DRAFTED, PENDING_APPROVAL, APPROVED, SUBMITTED, CLOSED_NO_FILE, REJECTED. SHOULD: EXPIRED, PORTAL_REJECTED. A transition table in code; illegal transitions raise and are audited.

## 6. Who decides what

| Model | Code | Person |
|---|---|---|
| Plan and routing; which hypotheses and drill-downs; which passages; narrative wording; semantic claim check | Indicators, sums, windows, thresholds; data quality; payload mapping and hash; permissions; budgets; approval verification; submission | File or no-file; approve, edit or reject the exact payload; accept lessons |
