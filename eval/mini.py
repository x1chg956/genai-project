"""Phase 1 mini evaluation (docs/02 Phase 1 task 10): `make mini-eval`.

Runs each dev case through the C1 graph unattended, exactly as `make run-case` would (live
model, stdio servers, Postgres, tracing), answering the gates from the case's gate_script.
A case without a gate_script is answered `needs_info`, so nothing is ever filed by default.
Writes one run record per case and results.jsonl under runs/<exp_id>/, then prints the table.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from auditguard.cli import live_deps, load_alert
from auditguard.config import REPO_ROOT, ConfigError, Settings, load_settings
from auditguard.graph.deps import GraphDeps
from auditguard.graph.single_agent import checkpointer, compile_graph
from auditguard.tracing import flush_traces, require_live_tracing, trace_config
from eval.cases import EvalCase, GateScript, load_cases
from eval.checkers import Check, run_checks

DEFAULT_CASES = REPO_ROOT / "eval" / "cases" / "dev" / "phase1.jsonl"
ANALYST = "ANL-SYN-EVAL"
APPROVER = "PO-SYN-EVAL"
MAX_GATE_ANSWERS = 6


def gate_answer(view: dict[str, Any], script: GateScript | None) -> dict[str, Any]:
    if view["kind"] == "decision":
        if script is None:
            return {"decision": "needs_info", "decided_by": ANALYST}
        return {
            "decision": script.decision,
            "reason_code": script.reason_code,
            "rationale": script.rationale,
            "decided_by": ANALYST,
        }
    action = script.approval if script else "reject"
    return {"action": action, "approver_id": APPROVER}


async def run_one(case: EvalCase, deps: GraphDeps, settings: Settings) -> dict[str, Any]:
    run_id = f"RUN-{uuid.uuid4().hex[:12]}"
    alert = load_alert(settings, case.alert_id or "")
    started = time.monotonic()
    trace_ids: list[str] = []
    async with checkpointer(settings) as saver:
        app = compile_graph(deps, saver)
        base = {"configurable": {"thread_id": run_id}}
        payload: Any = {
            "case_id": alert.alert_id,
            "run_id": run_id,
            "alert": alert,
            "requester_id": ANALYST,
            "status": "NEW",
            "errors": [],
        }
        for _ in range(MAX_GATE_ANSWERS + 1):
            trace_id = uuid.uuid4()
            trace_ids.append(str(trace_id))
            config = {
                **base,
                **trace_config(
                    settings,
                    run_id=trace_id,
                    run_name="mini-eval",
                    metadata={"case_id": case.case_id, "run_id": run_id},
                ),
            }
            out = await app.ainvoke(payload, cast(RunnableConfig, config))
            interrupts = out.get("__interrupt__")
            if not interrupts:
                break
            payload = Command(resume=gate_answer(dict(interrupts[0].value), case.gate_script))
        state = dict((await app.aget_state(cast(RunnableConfig, base))).values)
    flush_traces()
    state["_latency_s"] = round(time.monotonic() - started, 1)
    state["_trace_ids"] = trace_ids
    state["_run_id"] = run_id
    return state


def summarise(
    case: EvalCase, state: dict[str, Any], checks: list[Check], settings: Settings, deps: GraphDeps
) -> dict[str, Any]:
    stats = state.get("agent_stats") or {}
    result = state.get("result")
    return {
        "case_id": case.case_id,
        "alert_id": case.alert_id,
        "run_id": state.get("_run_id"),
        "trace_ids": state.get("_trace_ids", []),
        "status": state.get("status"),
        "recommendation": result.recommendation if result else None,
        "decision": state["decision"].decision if state.get("decision") else None,
        "checks": {c.name: {"status": c.status, "detail": c.detail} for c in checks},
        "tool_calls": stats.get("tool_calls"),
        "invalid_tool_calls": stats.get("invalid_tool_calls"),
        "structured_retries": stats.get("structured_retries"),
        "input_tokens": stats.get("input_tokens"),
        "output_tokens": stats.get("output_tokens"),
        "stopped_by": stats.get("stopped_by"),
        "latency_s": state.get("_latency_s"),
        "errors": [e.model_dump(mode="json") for e in state.get("errors", [])],
        "config_hash": settings.config_hash,
        "prompt_sha256": deps.prompt.sha256,
        "models": {role: settings.models[role].id for role in ("specialist", "judge")},
    }


COLUMNS = (
    "case_id",
    "status",
    "recommendation",
    "tool_calls",
    "invalid_tool_calls",
    "structured_retries",
    "tokens",
    "latency_s",
)
CHECKS = ("disposition", "rules", "numbers", "citations", "trajectory", "final_state")


def table(rows: list[dict[str, Any]]) -> str:
    head = [*COLUMNS, *CHECKS]
    lines = [" | ".join(head), " | ".join("---" for _ in head)]
    for r in rows:
        cells = [
            str(
                r.get(c)
                if c != "tokens"
                else f"{r.get('input_tokens') or 0}+{r.get('output_tokens') or 0}"
            )
            for c in COLUMNS
        ]
        cells += [r.get("checks", {}).get(c, {}).get("status", "-") for c in CHECKS]
        lines.append(" | ".join(cells))
    ran = [r for r in rows if r.get("checks")]
    for name in ("numbers", "citations"):
        scored = [r for r in ran if r["checks"][name]["status"] != "SKIP"]
        passed = sum(1 for r in scored if r["checks"][name]["status"] == "PASS")
        lines.append(f"{name}-match: {passed}/{len(scored)} drafts")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.mini")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "runs")
    args = parser.parse_args(argv)
    warnings.filterwarnings("ignore", message=".*langchain.mcp.*")
    try:
        cases = load_cases(args.cases)
        settings = load_settings()
        require_live_tracing(settings)
        deps = live_deps(settings)
    except (ConfigError, ValueError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    exp_id = f"mini-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir = args.out / exp_id
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = asyncio.run(run_all(cases, deps, settings, out_dir))
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")
    print(f"\nresults: {out_dir.relative_to(REPO_ROOT)}/results.jsonl\n")
    print(table(rows))
    return 0


async def run_all(
    cases: list[EvalCase], deps: GraphDeps, settings: Settings, out_dir: Path
) -> list[dict[str, Any]]:
    """All cases in one event loop: the model client is shared and bound to its loop."""
    rows: list[dict[str, Any]] = []
    for case in cases:
        if case.alert_id is None:
            rows.append({"case_id": case.case_id, "status": "NOT_RUN: no alert yet (role 1)"})
            print(f"{case.case_id}: not run, no alert_id yet")
            continue
        print(f"{case.case_id}: running {case.alert_id} ...", flush=True)
        try:
            state = await run_one(case, deps, settings)
        except Exception as exc:  # a failed run is recorded, never dropped (docs/10 §3)
            rows.append(
                {
                    "case_id": case.case_id,
                    "alert_id": case.alert_id,
                    "status": f"RUN_FAILED: {type(exc).__name__}: {str(exc)[:200]}",
                }
            )
            continue
        checks = run_checks(case, state, settings.budgets.case.tool_calls)
        row = summarise(case, state, checks, settings, deps)
        (out_dir / f"{row['run_id']}.json").write_text(
            json.dumps(
                {
                    **row,
                    "draft": state["result"].draft.model_dump(mode="json")
                    if state.get("result")
                    else None,
                    "tool_records": state.get("tool_records", []),
                    "agent_stats": state.get("agent_stats"),
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        rows.append(row)
    return rows


if __name__ == "__main__":
    sys.exit(main())
