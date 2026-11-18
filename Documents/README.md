# AuditGuard

Evidence-grounded multi-agent assistant for AML alert investigation and STR preparation (GenAI capstone, team of four).

> **Synthetic data only. Mock filing portal only. Not legal advice. Not validated on real alerts.**

## Start everything with one command

```bash
cp .env.example .env          # add your OPENROUTER_API_KEY; never commit .env
docker compose up --build
```

This starts the Chainlit UI, the FastAPI app (graph, hooks, metrics), four MCP servers, PostgreSQL, Qdrant, Prometheus and Grafana. To add Langfuse tracing: `docker compose --profile langfuse up --build`.

| Service | URL |
|---|---|
| Case UI (Chainlit) | http://localhost:8000 |
| API docs (FastAPI) | http://localhost:8080/docs |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |

First run: `docker compose run --rm app python -m auditguard.seed` generates the synthetic ledger, and `docker compose run --rm app python -m auditguard.ingest` fetches and indexes the corpus from `corpus/manifest.yaml` (files are fetched by script, not committed).

## Repository layout

```
auditguard/        app: graph, agents, code nodes, hooks, guardrails, API
mcp_servers/       transactions, aml_engine, regulatory_kb, filing_portal
rules/IN/          rule YAML files + tests
prompts/           six-slot prompt files (versioned by hash)
configs/           one versioned config + one file per experiment arm
eval/              harness, judge, red-team suite (held-out cases are NOT here)
corpus/            manifest.yaml with URLs and checksums
docs/              architecture, threat model, memory policy, evaluation report, logs
infra/             compose, Prometheus, Grafana dashboards and alert rules
.github/workflows/ CI: push, pull-request and nightly tiers
```

## Run the evaluation

```bash
make test                         # rules, MCP tools, approval/hash, PII sentinel
make eval CONFIG=configs/e1_c2.yaml SPLIT=dev
make eval-all SPLIT=heldout REPEATS=3     # needs HELDOUT_DIR mounted, live models, cost cap
```

## Documents

- `docs/architecture.md`: diagram and numbered walkthrough
- `docs/threat_model.md` and `docs/memory_policy.md`
- `docs/evaluation_report.pdf`, `docs/experiment_log.md`, `docs/contribution_log.md`
- `docs/tool_catalogue.md`: MCP tools with schemas and tests

## Team

| Role | Name |
|---|---|
| Product and evaluation lead | [Name] |
| Knowledge, memory and tools | [Name] |
| Agents and orchestration | [Name] |
| Control, security and deployment | [Name] |

AI coding tools wrote parts of the implementation; problem framing, design, evaluation and interpretation are the team's own.
