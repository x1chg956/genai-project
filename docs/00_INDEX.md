# AuditGuard specs: index and reading order

These documents tell Claude Code (and the team) what to build. `CLAUDE.md` at the repo root holds the rules that apply to every task.

| # | File | Read it when |
|---|---|---|
| 01 | `01_PRODUCT_BRIEF.md` | Always first: problem, users, scope, limits |
| 02 | `02_BUILD_PHASES.md` | Before every task: phases, tasks, acceptance checks |
| 03 | `03_ARCHITECTURE.md` | Building graphs, state or node wiring |
| 04 | `04_DATA_AND_RULES.md` | Generator, database schema, rule engine |
| 05 | `05_MCP_SERVERS.md` | Any MCP server or tool |
| 06 | `06_AGENTS_AND_PROMPTS.md` | Any agent or prompt |
| 07 | `07_RETRIEVAL.md` | Corpus, ingestion, search |
| 08 | `08_APPROVAL_GUARDRAILS_HOOKS.md` | Gates, hashing, guardrails, hooks |
| 09 | `09_MEMORY_AND_REFLEXION.md` | Long-term memory, lessons |
| 10 | `10_EVALUATION.md` | Cases, checkers, judge, harness, experiments |
| 11 | `11_DEPLOYMENT_AND_CI.md` | Config, Compose, API, UI, metrics, CI |
| 12 | `12_CONVENTIONS.md` | Code style, tests, errors, logging |
| 13 | `13_MODEL_PROVIDERS.md` | Model endpoints: local server by default, per-role config, `llm-check` |
| — | `PROGRESS.md` | Update after every task |
| — | `KICKOFF_PROMPTS.md` | Prompts to paste into Claude Code, phase by phase |
| — | `../.env.example` | Environment variable names (no secrets); copy to `.env` |

## The build strategy in one paragraph

Build a thin vertical slice with **one ReAct agent** that takes one synthetic alert all the way to a mock receipt (Phase 1). Then make that single agent complete and measurable at full scale (Phase 2). It becomes the C1 baseline, and a fixed workflow is built beside it as C0 (Phase 3). Only then split the same tools across a **supervisor and specialists** (Phase 4, C2), and add memory and Reflexion (Phase 5), security (Phase 6), deployment (Phase 7) and the experiments (Phase 8). The tools, MCP servers, data and evaluation harness are shared by C0, C1 and C2 from the start, so the team-vs-single-agent comparison the course asks for falls out of the build order.

## Who writes what (separations from the course guide)

| Artefact | Written by | Claude Code may |
|---|---|---|
| Typology spec (`data/generator/typology_spec.yaml`) | Role 1 (human) | Draft a schema and an example entry only |
| Rule spec (`docs/04_DATA_AND_RULES.md` §3) | Role 2 with domain reviewer | Implement it |
| Development cases (`eval/cases/dev/`) | Role 1 | Build the scaffold and the generator; role 1 reviews every expected outcome |
| Held-out cases (outside the repo) | Role 1 only | Never read or write |
| Red-team attacks | Role 1 only | Build the attack runner |
| Lesson approvals | A human reviewer | Build the review CLI |
