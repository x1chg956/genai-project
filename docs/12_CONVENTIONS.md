# 12 Conventions

## Code
- Python 3.11+, type hints everywhere, `mypy --strict` on `auditguard/` and `mcp_servers/`.
- `ruff` for lint and format. Line length 100.
- Pydantic v2 for every boundary (tool I/O, hand-offs, config, API).
- Money: `Decimal` with `ROUND_HALF_EVEN` at two places only when presenting; never `float`.
- Time: store UTC (`datetime` with tzinfo); convert with `zoneinfo.ZoneInfo("Asia/Kolkata")` for day and month windows.
- No global state; dependencies passed in (settings, clients) to keep nodes testable.
- Pure functions for rules, canonicalisation, hashing and checkers.

## Errors
- MCP tools return `ToolError`, never raise to the caller.
- Graph nodes catch tool errors and write them to `state.errors`; routing decides retry or replan.
- Never swallow an exception silently; log with `correlation_id`.

## Logging and traces
- Structured JSON logs (`structlog`), always with `case_id`, `run_id`, `node`, `correlation_id`.
- Scrub PII before logging; logs only ever contain tokens, never raw identifiers.
- Traces via Langfuse or LangSmith callbacks configured in `llm.py`; synthetic data only.
- Model clients are created only in `llm.py`; never hard-code a base URL or model ID.

## Tests
- `tests/` mirrors the package. Unit tests for every rule (≥5), every tool (happy + invalid), canonicalisation, approval verification, transitions.
- Property tests (`hypothesis`) for rules and canonical JSON.
- Graph tests use a fake model (`langchain_core` fake chat model) and scripted tool results; no live LLM in unit tests.
- Live-model tests are marked `@pytest.mark.live` and excluded by default.

## Git
- Conventional commits. One logical change per commit.
- Tags: `c1-frozen`, `playbook-<date>`, `final-<date>`.
- Never commit `.env`, `corpus/raw/`, `runs/`, or anything from the held-out folder.

## Working with Claude Code
- One phase task per session where possible; start from `KICKOFF_PROMPTS.md`.
- Ask before changing a spec. If you change one, update the doc in the same commit.
- Prefer small, reviewable diffs; run `make test` before each commit.
- After each task, append to `docs/PROGRESS.md`.
