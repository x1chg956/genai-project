# Progress log

Claude Code appends one line per completed task. Humans tick the exit gates.

| Date | Phase | Task | What changed | Tests added | By |
|---|---|---|---|---|---|
| 2026-11-21 | 0 | 1. Project skeleton | `pyproject.toml` (uv, Python 3.12 pinned, exact dependency pins), ruff + mypy strict + pytest config, `Makefile` with every CLAUDE.md target (later-phase targets exit with "not implemented until Phase N") | — | Claude Code |
| 2026-11-21 | 0 | 2. Config | `configs/base.yaml` per docs/11 §1 + docs/13 §2; `auditguard/config.py`: typed `Settings`, `${VAR:-default}` expansion after YAML parse, env over `.env`, experiment deep-merge, `config_hash` with masked secrets, per-role `structured_method` fallback | `tests/test_config.py` (21) | Claude Code |
| 2026-11-21 | 0 | 3. Model factory + llm-check | `auditguard/llm.py`: `get_model`, `get_structured_model`, `get_guard` (completions client for `completions_raw`), per-model `stop`; `auditguard/guardrails/llama_guard.py` + `prompts/guard_llama_guard_3.txt`; `scripts/llm_check.py` (6 checks + guard probes, never prints keys). `make llm-check`: all required checks PASS for supervisor/specialist/decomposer (Qwen, 54k-token prompt accepted), judge (Gemma, with stop + format prompt) and guard (2/2 probes) | `tests/test_llm.py` (12), `tests/guardrails/test_llama_guard.py` (9) | Claude Code |
| 2026-11-21 | 0 | 4. Compose | `infra/docker-compose.yml` (postgres:17.6-alpine on host 5433, qdrant v1.20.0, health checks, init script for auditguard + groundtruth with per-role CONNECT), root `docker-compose.yml` include; verified healthy and isolated in a throwaway project | manual check (4 isolation probes PASS) | Claude Code |
| 2026-11-21 | 1 | 1. Schemas | `auditguard/schemas/`: all docs/03 §3 Phase 1 models plus `KycProfile`, `Decision`, `SingleAgentResult`, `HandOff`; Decimal money (floats rejected), UTC-aware timestamps, evidence required for material sentences and decided hypotheses | `tests/schemas` (19) | Claude Code |
| 2026-11-21 | 1 | 3. Rule engine v0 | R01, R04, R07 as pure functions in `mcp_servers/aml_engine/engine/rules/`, YAML in `rules/IN/` with an `interpretation` field for role 2 review, rule-pack loader. Written from docs/04 §3 before the generator existed | `tests/rules` (37: ≥5 per rule incl. boundary, empty, defect) | Claude Code |
| 2026-11-21 | 1 | 2. Generator v0 | `data/generator/`: typology spec schema + ONE example entry (role 1 writes the rest), seeded pure generator (tiny: 10 accounts, 60 days), fictional names + deny-list, writer to `ledger.*` (auditguard) and `groundtruth.*` (groundtruth), `make seed`. Verified on a throwaway Postgres: rerun replaces data, NUMERIC money, UTC timestamps, app role denied CONNECT to groundtruth | `tests/generator` (27) | Claude Code |
| 2026-11-21 | 1 | 4. MCP servers v0 | Four FastMCP 4.1.8 servers at their Phase 1 tool sets: `transactions` (get_transactions, get_account_summary, get_counterparty_flows), `aml_engine` (evaluate_rules, get_rule_metadata), `regulatory_kb` (search_passages BM25, get_passage), `filing_portal` (prepare_payload, submit_approved_payload, get_receipt; own SQLite). Shared `ToolGuard` middleware: HMAC agent tokens + per-caller allow-list + case scope; every failure a structured `ToolError` (isError). Also `auditguard/approval/canonical.py` + `records.py` (HMAC approval records, Phase 1 SQLite store), `auditguard/security/tokens.py`, `auditguard/ledger_ref.py`, read-only DB role `auditguard_ro`. Verified on a throwaway Postgres: app data readable, groundtruth `permission denied`, writes refused, stdio chain get_transactions → evaluate_rules works | `tests/mcp_servers` (87: happy + invalid per tool, wrong token, out of scope, stale hash, self-approval, expired, idempotent, transport parity in-memory/stdio/HTTP, groundtruth unreachable static + 3 live), `tests/approval` (19), `tests/security` (9), `tests/test_ledger_ref.py` (7) | Claude Code |
| 2026-11-21 | 0 | 5. CI | `.github/workflows/ci.yml`: uv sync --locked, make lint, make test on push and PR | — | Claude Code |

## Exit gates

- [ ] Phase 0: skeleton installs, lints, tests; Postgres and Qdrant healthy
- [ ] Phase 1: one alert → receipt with CLI approval; edit voids approval; resume works; 6 dev cases run, numbers-match 100%
- [ ] Phase 2: rules 01–08 + property tests; Recall@5 staircase; harness on all dev cases; ground truth unreachable; tag `c1-frozen`
- [ ] Phase 3: C0 runs; first C0 vs C1 table in experiment log
- [ ] Phase 4: plan → refuted hypothesis → replan trace; critic catches planted error; E1 dev table C0/C1/C2
- [ ] Phase 5: stale-memory trap passes; one rejected lesson logged; playbook frozen
- [ ] Phase 6: E2 on dev attacks; every defence layer toggleable
- [ ] Phase 7: clean-machine `docker compose up --build`; alert fires; one failed CI run explained
- [ ] Phase 8: judge kappa; E1–E6 held-out × 3; results table filled

## Decisions and open questions

| Date | Question or decision | Owner | Status |
|---|---|---|---|
| 2026-11-21 | Local endpoint verified: oMLX at http://127.0.0.1:8888/v1, model `Qwen3.5-4B-4bit`, key required; chat, tool calling and json_schema output work; thinking must be turned off with `chat_template_kwargs.enable_thinking=false` | | done |
| | A 4B model may be too weak for the supervisor and drafter; decide after Phase 1 whether to serve a larger local model or move those roles to OpenRouter | | open |
| 2026-11-21 | Local judge/guard tested on oMLX (docs/13 §1b): `gemma-3-1b-it-4bit` fails as judge (approved a wrong amount); `Llama-Guard-3-1B-chat-mlx-4bit` fails over chat (oMLX template) but scores 5/5 via `/completions` with the official raw prompt. Decision: OpenRouter stays default for judge and guard; local guard available via `GUARD_MODE=completions_raw` | | done |
| 2026-11-21 | Config decisions before Phase 0: `budgets.case` everywhere (docs/06 fixed); `structured_method` per role with provider fallback; later-phase fields (`fallback.id`, `corpus.version`, `mcp.servers`) optional/null; `databases` + `qdrant` sections added; local Postgres on 5432 via `scripts/db_bootstrap.sh`, Compose Postgres on host 5433 | | done |
| | Disposition vocabularies differ: cases use `escalate_for_review/no_action/insufficient_data`, `Decision` uses `file/no_file/needs_info`; map explicitly in the disposition checker | | open (Phase 1) |
| 2026-11-21 | `KycProfile` added to the Phase 1 schemas (rule signature needs it; R01/R04/R07 ignore it) | | done |
| 2026-11-21 | Single agent output is one `SingleAgentResult` (docs/03 §2); docs/02 Phase 1 task 6 wording to be aligned | | done |
| 2026-11-21 | Rule interpretations to review (role 2): windows are W consecutive Asia/Kolkata calendar days (last − first ≤ W−1); R01 L inclusive, T exclusive; R04 FIFO matching over the slice, H inclusive in elapsed hours, residual = unmatched credit value; R07 credits without counterparty are not senders. Recorded in each `rules/IN/*.yaml` `interpretation` | Role 2 | open |
| 2026-11-21 | Spec example vs R04: `rapid_pass_through.standard` draws share_out 0.85–0.99, so 1–15% of inflows stay and the residual (e.g. INR 8,891.53 at seed 42) exceeds R04's residual_balance_max of INR 1,000. ALR-SYN-0001 therefore does not fire R04. Neither the spec nor the rule was changed: role 1 and role 2 decide which one reflects the typology | Role 1 + 2 | open |
| | `typology_spec.yaml` needs the fan_in entry (and payroll look-alike review) before the 6 Phase 1 dev cases can be scaffolded | Role 1 | open |
| | Proposal v4.1 vs specs: specialist model (gpt-4.1-mini vs local Qwen), drafting temperature (0 vs ≤0.3), E4c arms (6 vs 4), supervisor tools (get_prior_reports vs none) | | open |
| 2026-11-21 | Judge and guard moved to local oMLX by user decision: judge `gemma-3-1b-it-4bit`, guard `Llama-Guard-3-1B-chat-mlx-4bit` (`completions_raw`). All roles now local; OpenRouter is an optional switch. Stated limits: 1B judge not yet validated; Llama Guard 3 instead of 4 | User | done |
| | Does the 1B Gemma judge reach κ ≥ 0.6 in calibration? If not: larger Gemma (12B/27B) or OpenRouter | | open (Phase 4/8) |
| 2026-11-21 | MCP stack: `langchain-mcp-adapters` is unmaintained (moved to `langchain.mcp`) and needs `mcp<2`, which FastMCP 4 cannot use. Decision: `langchain[mcp]==1.4.9` (MCPAdapter) + `fastmcp==4.1.8`; CLAUDE.md, docs/05 and the kickoff prompt updated | User | done |
| 2026-11-21 | Spec clarifications made in session 2 (docs/05 updated): `prepare_payload` takes the `payload` itself (the portal must store the bytes it re-hashes at submit); every tool, including get_rule_metadata/search_passages/get_passage, takes `agent_token` + `case_id`; Phase 1 case scope = case_id is the alert_id and only the alerted account is readable; `get_transactions` returns a digest-bound `ledger_ref` that `evaluate_rules` consumes; approval records live in a SQLite store in Phase 1 that the portal opens read-only | | review |
| | `filing_portal.form_limits` (200,000 bytes, 8,000 chars of grounds) are placeholders; confirm the FIU-IND STR form limits | Domain reviewer | open |
| | No corpus yet: regulatory_kb needs `corpus/processed/passages.jsonl` from `make ingest` (2 docs for Phase 1; manifest needs domain-reviewer sign-off). Tests use a synthetic fixture corpus | Domain reviewer | open |
| | Ingest data defects (duplicates, reversals) vs `ledger_ref`: evaluate_rules reads raw rows and rejects duplicates; when generator v1 adds defects (Phase 2), the ref must name the cleaned slice | | open (Phase 2) |
| | Cash threshold T for R01 tagged with its PML Rules source | Domain reviewer | open |
| | LangSmith or Langfuse | | open |
