# 11 Deployment and CI

## 1. The one versioned config (`configs/base.yaml`)

```yaml
config_version: 1
profile: IN
timezone: Asia/Kolkata
providers:                                        # full spec: docs/13_MODEL_PROVIDERS.md
  local:      {base_url: "${LLM_BASE_URL:-http://127.0.0.1:8888/v1}", api_key: "${LLM_API_KEY}", timeout_s: 120,
               extra_body: {chat_template_kwargs: {enable_thinking: false}}, structured_method: json_schema}
  openrouter: {base_url: "https://openrouter.ai/api/v1", api_key: "${OPENROUTER_API_KEY:-}", timeout_s: 60}
models:
  supervisor: {provider: local, id: "${LLM_MODEL:-Qwen3.5-4B-4bit}", temperature: 0, max_tokens: 2000}
  specialist: {provider: local, id: "${LLM_MODEL:-Qwen3.5-4B-4bit}", temperature: 0.2, max_tokens: 3000}
  decomposer: {provider: local, id: "${LLM_MODEL:-Qwen3.5-4B-4bit}", temperature: 0, max_tokens: 400}
  judge:      {provider: "${JUDGE_PROVIDER:-local}", id: "${JUDGE_MODEL:-gemma-3-1b-it-4bit}", temperature: 0, max_tokens: 1500}   # not yet validated: docs/13 §1b
  guard:      {provider: "${GUARD_PROVIDER:-local}", id: "${GUARD_MODEL:-Llama-Guard-3-1B-chat-mlx-4bit}", temperature: 0,
               mode: "${GUARD_MODE:-completions_raw}", prompt_template: "${GUARD_PROMPT_TEMPLATE:-prompts/guard_llama_guard_3.txt}"}
  fallback:   {provider: openrouter, id: null, enabled: false}   # pin id before enabling; a switch is logged and flagged
prompts: {single_agent: prompts/single_agent.md, supervisor: prompts/supervisor.md, investigator: prompts/investigator.md,
          researcher: prompts/researcher.md, drafter: prompts/drafter.md, critic_judge: prompts/critic_judge.md}
graph: c1                                         # c0 | c1 | c2
budgets:
  case: {steps: 12, tool_calls: 20, tokens: 60000, seconds: 180, cost_usd: 0.50}
  critic_revisions: 2
retrieval: {bm25: true, vector: true, rerank: true, decompose: true, k_bm25: 20, k_vector: 20, k_final: 5, rrf_k: 60, score_floor: 0.2}
rules: {pack: rules/IN, version: "1.0.0"}
corpus: {manifest: corpus/manifest.yaml, version: null}   # set by the first ingest (Phase 1)
databases:                                        # from .env; local Postgres on 5432, Compose Postgres on host 5433
  app:         {host: "${POSTGRES_HOST:-localhost}", port: "${POSTGRES_PORT:-5432}", name: "${POSTGRES_DB:-auditguard}",
                user: "${POSTGRES_USER:-auditguard}", password: "${POSTGRES_PASSWORD:-}"}
  groundtruth: {host: "${POSTGRES_HOST:-localhost}", port: "${POSTGRES_PORT:-5432}", name: "${GROUNDTRUTH_DB:-groundtruth}",
                user: "${GROUNDTRUTH_USER:-groundtruth}", password: "${GROUNDTRUTH_PASSWORD:-}"}   # eval + generator only
qdrant: {url: "${QDRANT_URL:-http://localhost:6333}"}
guardrails:
  input: {schema: true, pseudonymise: true, prompt_guard: false, llama_guard: false, scope: true}
  output: {provenance: true, pii: true, neutral_wording: true, tipping_off: true}
memory: {short_term: {keep_turns: 12}, long_term: {enabled: false, max_lessons: 2}}
approval: {ttl_minutes: 120}
mcp:
  transport: stdio                                 # http in Docker
  servers: {}                                      # filled per server in Phase 1 (transactions, aml_engine, regulatory_kb, filing_portal)
  description_hashes: {}                           # pinned in Phase 6
```

`auditguard/config.py` loads base + optional experiment override into a typed `Settings` model and exposes `config_hash`. Fields that a later phase fills (`fallback.id`, `corpus.version`, `mcp.servers`) are optional and may be null; the code that needs one fails with a clear message if it is still unset. The groundtruth connection is loaded only by `eval/` and `data/generator/`; the app and MCP servers never read `databases.groundtruth`. Local roles and databases are created by `scripts/db_bootstrap.sh`. Every run stores the config hash, prompt file hashes, model IDs and seed.

## 2. Docker Compose (`infra/docker-compose.yml`, final state)

Services: `postgres` (DBs `auditguard` + `groundtruth`, init script; published on host port **5433** so it never clashes with a local Postgres on 5432), `qdrant`, `mcp-transactions`, `mcp-aml-engine`, `mcp-regulatory-kb`, `mcp-filing-portal`, `app` (FastAPI: graph, hooks, metrics), `ui` (Chainlit), `prometheus`, `grafana` (provisioned dashboard + alert). Profile `langfuse`: `langfuse` + its DB. Health checks on all; pinned image tags; secrets from `.env`. `docker compose up --build` is the one command. Services that call models (`app`, `eval`) get `LLM_BASE_URL=http://host.docker.internal:8888/v1` and `extra_hosts: ["host.docker.internal:host-gateway"]`, because `127.0.0.1` inside a container is the container itself. The local model server runs on the host and is not part of Compose.

The held-out folder is mounted **only** into an `eval` one-off container, never into `app` or MCP containers.

## 3. API (`auditguard/api/`)

| Endpoint | Purpose |
|---|---|
| `POST /cases` | create case from alert, start run, return case_id and run_id |
| `GET /cases/{id}/events` | SSE stream of node events, plan, tool calls (scrubbed) |
| `POST /cases/{id}/decision` | resume decision_gate (analyst role) |
| `POST /cases/{id}/approval` | resume approval_gate (approver role; requester refused) |
| `GET /cases/{id}` | state summary, versions, hashes |
| `GET /health`, `GET /metrics` | health; Prometheus |

Auth: simple bearer tokens per role for the project (documented as not production auth).

## 4. UI (Chainlit, `auditguard/ui/`)

Screens: triage queue · case view (plan with versions, indicators with transaction IDs, hypotheses with status, passages with versions) · decision form · approval view (payload, SHA-256, diff, approve/edit/return/reject) · memory view with delete.

## 5. Metrics and dashboard

`prometheus_client` metrics: `cases_total{outcome}`, `rules_fired_total{rule_id}`, `critic_rejections_total`, `pending_approvals`, `tool_calls_total{server,tool,status}`, `tool_errors_total{server,code}`, `case_latency_seconds` (histogram), `case_cost_usd` (histogram), `tokens_total{model}`, `guardrail_blocks_total{layer}`.

Grafana panels: task success, rules fired, critic rejections, pending approvals, tool errors and retries, median and p95 latency, cost per case, MCP success rate. **Alert:** `rate(tool_errors_total[5m])` above threshold for one server for 2 minutes (demo by stopping a server).

## 6. CI (`.github/workflows/`)

| Tier | Trigger | Runs |
|---|---|---|
| push | every push | ruff, mypy, rule unit + property tests (exact Decimal), MCP tool tests, approval/hash tests, PII sentinel scan, secret scan (gitleaks), Recall@5 gate on local embeddings, ≥80% coverage on rule engine and approval code |
| pr | pull request | replay-cache smoke run of 15 dev cases at temperature 0, no live spend; fail on any unauthorised write or unresolved citation |
| nightly | schedule or manual | full evaluation with live models under a cost cap; thresholds from the agreed baseline. GitHub-hosted runners cannot reach your local model server: run this tier on a self-hosted runner on the machine that serves `:8888`, or point `LLM_BASE_URL` at a hosted provider for CI |

Replay cache key: `(prompt_hash, model_id, params_hash, input_hash)`. A cache miss in the PR tier fails with "refresh the replay cache via the nightly/manual job". Keep one deliberately failed run and explain it in the experiment log.
