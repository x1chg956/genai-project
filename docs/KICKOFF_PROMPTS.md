# Kickoff prompts for Claude Code

Paste one prompt per session. Each assumes `CLAUDE.md` and `docs/` are in the repo. Start each session in plan mode, review the plan, then let it implement.

Before Phase 0: `cp .env.example .env` and fill in `LLM_API_KEY` for the local oMLX server (and `OPENROUTER_API_KEY` for the judge). Make sure the local server is running on port 8888.

## Phase 0
> Read CLAUDE.md, docs/00_INDEX.md and Phase 0 in docs/02_BUILD_PHASES.md. Plan the skeleton (pyproject with uv, Makefile targets, configs/base.yaml with a typed loader per docs/11 §1, auditguard/llm.py and scripts/llm_check.py per docs/13 (default provider: the local server at http://127.0.0.1:8888/v1, configurable via LLM_BASE_URL), compose with Postgres (auditguard + groundtruth databases) and Qdrant, CI lint+test). Show me the plan before writing files. Then implement, run the Phase 0 acceptance checks including make llm-check against Qwen3.5-4B-4bit with thinking turned off, and update docs/PROGRESS.md. Never print the API key.

## Phase 0, follow-up: tracing and evidence stub
> Do Phase 0 tasks 7–8 from docs/02: tracing in auditguard/llm.py per docs/11 §7 (default LangSmith, backend from config, a live run refuses backend `none`), and the `docs-evidence` Makefile stub per docs/11 §8. Rerun the Phase 0 acceptance checks, including one traced model call with its trace ID. Never print a key.

## Phase 1, session 1: schemas, generator, rules
> Do Phase 1 tasks 1–3 from docs/02_BUILD_PHASES.md. Use docs/03 §3 for schemas and docs/04 for the generator (tiny profile) and rules R01, R04, R07. Write the typology_spec.yaml schema and ONE example entry only; I will write the rest. Rules must be pure functions with ≥5 Decimal-exact tests each. Do not read the groundtruth database when writing rules.

## Phase 1, session 2: MCP servers
> Do Phase 1 task 4: the four FastMCP servers at their Phase 1 tool sets per docs/05, stdio transport, structured ToolError results, agent tokens and case scope checks, and the listed tests. Check the current FastMCP and `langchain.mcp` docs before coding.

## Phase 1, session 3: single agent and graph
> Do Phase 1 tasks 5–8: code nodes ingest_validate, build_payload, submit; the single ReAct agent with prompts/single_agent.md (six slots, docs/06); the graph in docs/03 §2 (C1) with decision_gate and approval_gate using interrupt() per docs/08 §2; a SQLite checkpointer; and the CLI run-case command. Show the kill-and-resume and edit-voids-approval behaviour in tests.

## Phase 1, session 4: evidence generators and mini evaluation
> Do Phase 1 tasks 9–10. First the evidence generators per docs/11 §8 (R/C/W tags on every tool, `@pytest.mark.tool` markers, docs/tool_catalogue.md and docs/graph_c1.md committed, CI diff check). Then build the dev case scaffold format from docs/10 §2 and a minimal runner with the disposition, citations and numbers checkers. Create 6 case scaffolds with the expected-outcome fields EMPTY and marked TODO(role1). Every run must record a trace ID. Run the Phase 1 exit gate and report the results.

## Phase 2
> Do Phase 2 from docs/02, one task per commit, in order. Retrieval per docs/07, approval v1 per docs/08, harness v1 per docs/10 §3–4. When the exit gate passes, tag c1-frozen. Never read HELDOUT_DIR.

## Phase 3
> Build the C0 fixed workflow per docs/03 §2 over the same tools, add configs/experiments/c0_fixed.yaml, export docs/graph_c0.md, and run C0 vs C1 on dev cases through the harness. Write the results table into docs/PROGRESS.md and flag where C0 wins.

## Phase 4
> Build the C2 team per docs/03 §2 and docs/06 (supervisor + investigator + researcher fan-out + drafter + critic), reusing the same MCP servers, hooks and schemas. Keep budgets and the specialist model identical to C1. Seed the three replan cases, add configs/experiments/c1_strong.yaml, export docs/graph_c2.md, and run E1 (C0, C1, C1-strong, C2) on dev.

## Phase 5
> Implement docs/09: the LangGraph store namespaces, the memory policy page at docs/memory_policy.md, the Reflexion runner (dev only), the human review CLI and make freeze-playbook. Add the E3 and E6 configs.

## Phase 6
> Implement the layered guardrails and hooks v2 from docs/08 and the red-team runner that loads attacks from a folder I provide. Do not write attack cases. Make every layer toggleable and add the E2 configs. Draft docs/threat_model.md from the template in docs/08 §6.

## Phase 7
> Implement docs/11 §2–6: FastAPI with SSE, Chainlit screens, full Compose, metrics, the Grafana dashboard and alert, and the three CI tiers with the replay cache. Add the `llm` Compose profile per docs/11 §2. Then follow the README on a clean checkout (`docker compose --profile llm up --build`) and fix anything that does not work.

## Phase 8
> Add judge calibration (eval/calibrate.py, Cohen's kappa) and a report exporter that turns runs/ into the tables in docs/10 §7. Do not run held-out experiments yourself; give me the exact commands to run with HELDOUT_DIR mounted.

## Any time: review
> Review the last phase against CLAUDE.md's three rules and hard constraints, docs/02's acceptance checks, and the separations in docs/00. List violations with file and line, then fix them.
