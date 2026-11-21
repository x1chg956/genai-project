# Progress log

Claude Code appends one line per completed task. Humans tick the exit gates.

| Date | Phase | Task | What changed | Tests added | By |
|---|---|---|---|---|---|
| | 0 | | | | |

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
| | `KycProfile` needed by the rule signature but not in the Phase 1 schema list | | open (Phase 1) |
| | Single agent output: `SingleAgentResult` (docs/03) vs four objects (docs/02 Phase 1 task 6) | | open (Phase 1) |
| | Proposal v4.1 vs specs: specialist model (gpt-4.1-mini vs local Qwen), drafting temperature (0 vs ≤0.3), E4c arms (6 vs 4), supervisor tools (get_prior_reports vs none) | | open |
| 2026-11-21 | Judge and guard moved to local oMLX by user decision: judge `gemma-3-1b-it-4bit`, guard `Llama-Guard-3-1B-chat-mlx-4bit` (`completions_raw`). All roles now local; OpenRouter is an optional switch. Stated limits: 1B judge not yet validated; Llama Guard 3 instead of 4 | User | done |
| | Does the 1B Gemma judge reach κ ≥ 0.6 in calibration? If not: larger Gemma (12B/27B) or OpenRouter | | open (Phase 4/8) |
| | Cash threshold T for R01 tagged with its PML Rules source | Domain reviewer | open |
| | LangSmith or Langfuse | | open |
