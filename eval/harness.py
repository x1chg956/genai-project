"""Evaluation harness v1 (docs/10 §3): `make eval CONFIG=... SPLIT=dev [REPEATS=1]`.

Loads the experiment config (which graph and toggles), the split's cases, and runs each
case `repeats` times through the graph with a fresh run_id, answering every interrupt from
the case's gate_script (needs_info when there is none, so nothing is filed by default).
For each run it records a snapshot to `runs/<exp_id>/<run_id>.json` and one row in
`results.jsonl`: success, policy violations, checks, cost, tokens, latency, errors, trace
IDs, config and prompt hashes, model IDs and the seed. Failed and timed-out runs are
recorded, never dropped. `python -m eval.report runs/<exp_id>` prints the table.

Every prompt sent to a model in the run is captured in memory and checked for raw PII
(docs/10 §4 `pii`), as is every audit-log message; the captured text is never written out.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
import warnings
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from auditguard.agents.single import with_callback
from auditguard.cli import live_deps, load_alert
from auditguard.config import REPO_ROOT, ConfigError, Settings, load_settings
from auditguard.graph.deps import GraphDeps
from auditguard.graph.single_agent import checkpointer, compile_graph
from auditguard.guardrails.pii import find
from auditguard.hooks.post_tool import AuditSink, MemoryAuditSink, ToolEvent
from auditguard.tracing import flush_traces, require_live_tracing, trace_config
from eval.cases import EvalCase, dev_cases, heldout_cases
from eval.checkers import Check, run_checks
from eval.mini import ANALYST, MAX_GATE_ANSWERS, gate_answer

BUILT_GRAPHS = {"c1"}


class PromptRecorder(AsyncCallbackHandler):
    """Keeps, in memory only, every text sent to a model during one run."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], **kwargs: Any
    ) -> None:
        for batch in messages:
            self.texts += [str(m.content) for m in batch]

    async def on_llm_start(
        self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any
    ) -> None:
        self.texts += prompts


@dataclass
class TeeAuditSink:
    """The configured audit sink plus an in-memory copy for this run's checks."""

    sink: AuditSink
    copy: MemoryAuditSink = field(default_factory=MemoryAuditSink)

    def append(self, event: ToolEvent) -> None:
        self.copy.append(event)
        self.sink.append(event)


def violations(
    state: dict[str, Any], prompts: list[str], events: list[ToolEvent], checks: list[Check]
) -> list[str]:
    """Policy violations in one run (docs/10 §7). A guardrail block is the defence working,
    not a violation; it is reported separately."""
    found: list[str] = []
    kinds = sorted({k for text in prompts for k in find(text)})
    if kinds:
        found.append(f"pii_in_prompt:{','.join(kinds)}")
    log_kinds = sorted({k for e in events for k in find(e.message)})
    if log_kinds:
        found.append(f"pii_in_log:{','.join(log_kinds)}")
    trajectory = next((c for c in checks if c.name == "trajectory"), None)
    if trajectory and "write tools" in trajectory.detail:
        found.append("agent_called_a_write_tool")
    forbidden = next((c for c in checks if c.name == "forbidden_claims"), None)
    if forbidden and forbidden.status == "FAIL":
        found.append("forbidden_claim")
    return found


async def run_case(
    case: EvalCase, deps: GraphDeps, settings: Settings, *, repeat: int
) -> dict[str, Any]:
    run_id = f"RUN-{uuid.uuid4().hex[:12]}"
    alert = load_alert(settings, case.alert_id or "")
    recorder = PromptRecorder()
    tee = TeeAuditSink(deps.audit)
    case_deps = replace(deps, audit=tee)
    started = time.monotonic()
    trace_ids: list[str] = []
    async with checkpointer(settings) as saver:
        app = compile_graph(case_deps, saver)
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
            traced = trace_config(
                settings,
                run_id=trace_id,
                run_name=f"eval:{case.case_id}",
                metadata={"case_id": case.case_id, "run_id": run_id, "repeat": str(repeat)},
            )
            config = with_callback(cast(RunnableConfig, {**base, **traced}), recorder)
            out = await app.ainvoke(payload, config)
            interrupts = out.get("__interrupt__")
            if not interrupts:
                break
            payload = Command(resume=gate_answer(dict(interrupts[0].value), case.gate_script))
        state = dict((await app.aget_state(cast(RunnableConfig, base))).values)
    flush_traces()
    state["_latency_s"] = round(time.monotonic() - started, 1)
    state["_trace_ids"] = trace_ids
    state["_run_id"] = run_id
    state["_prompts"] = recorder.texts
    state["_events"] = tee.copy.events
    return state


def row_for(
    case: EvalCase, state: dict[str, Any], settings: Settings, deps: GraphDeps, repeat: int
) -> dict[str, Any]:
    checks = run_checks(case, state, settings.budgets.case.tool_calls)
    found = violations(state, state.get("_prompts", []), state.get("_events", []), checks)
    scored = [c for c in checks if c.status != "SKIP"]
    stats = state.get("agent_stats") or {}
    result = state.get("result")
    blocks = [f for f in state.get("guard_findings", []) if f.get("severity") == "critical"]
    return {
        "case_id": case.case_id,
        "repeat": repeat,
        "alert_id": case.alert_id,
        "run_id": state.get("_run_id"),
        "trace_ids": state.get("_trace_ids", []),
        "status": state.get("status"),
        "success": bool(scored) and all(c.status == "PASS" for c in scored) and not found,
        "scored_checks": len(scored),
        "violations": found,
        "guard_blocks": len(blocks),
        "recommendation": result.recommendation if result else None,
        "checks": {c.name: {"status": c.status, "detail": c.detail} for c in checks},
        "tool_calls": stats.get("tool_calls"),
        "invalid_tool_calls": stats.get("invalid_tool_calls"),
        "structured_retries": stats.get("structured_retries"),
        "input_tokens": stats.get("input_tokens"),
        "output_tokens": stats.get("output_tokens"),
        "cost_usd": stats.get("cost_usd", "0"),
        "stopped_by": stats.get("stopped_by"),
        "latency_s": state.get("_latency_s"),
        "errors": [e.model_dump(mode="json") for e in state.get("errors", [])],
        "config_hash": settings.config_hash,
        "prompt_sha256": deps.prompt.sha256,
        "models": {role: settings.models[role].id for role in ("specialist", "judge")},
        "seed": case.seed,
        "lessons_used": [],
    }


def failed_row(case: EvalCase, repeat: int, status: str) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "repeat": repeat,
        "alert_id": case.alert_id,
        "status": status,
        "success": False,
        "violations": [],
        "errors": [{"error_code": status.split(":")[0], "message": status}],
    }


async def run_all(
    cases: list[EvalCase], deps: GraphDeps, settings: Settings, out_dir: Path, repeats: int
) -> list[dict[str, Any]]:
    """Every case and repeat in one event loop (the model client is bound to its loop)."""
    rows: list[dict[str, Any]] = []
    timeout = settings.budgets.case.seconds * 3  # whole graph, including gates and answer
    for repeat in range(1, repeats + 1):
        for case in cases:
            if case.alert_id is None:
                rows.append(failed_row(case, repeat, "NOT_RUN: no alert yet (role 1)"))
                print(f"{case.case_id}#{repeat}: not run, no alert_id yet")
                continue
            print(f"{case.case_id}#{repeat}: running {case.alert_id} ...", flush=True)
            try:
                state = await asyncio.wait_for(
                    run_case(case, deps, settings, repeat=repeat), timeout
                )
            except TimeoutError:
                rows.append(failed_row(case, repeat, f"TIMEOUT: over {timeout} s"))
                continue
            except Exception as exc:  # recorded, never dropped (docs/10 §3)
                status = f"RUN_FAILED: {type(exc).__name__}: {str(exc)[:200]}"
                rows.append(failed_row(case, repeat, status))
                continue
            row = row_for(case, state, settings, deps, repeat)
            snapshot = {
                **row,
                "draft": state["result"].draft.model_dump(mode="json")
                if state.get("result")
                else None,
                "tool_records": state.get("tool_records", []),
                "agent_stats": state.get("agent_stats"),
                "guard_findings": state.get("guard_findings", []),
            }
            (out_dir / f"{row['run_id']}.json").write_text(
                json.dumps(snapshot, indent=2, default=str), encoding="utf-8"
            )
            rows.append(row)
    return rows


def load_split(split: str) -> list[EvalCase]:
    return dev_cases() if split == "dev" else heldout_cases()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.harness")
    parser.add_argument("--config", type=Path, required=True, help="configs/experiments/<arm>.yaml")
    parser.add_argument("--split", choices=("dev", "heldout"), default="dev")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "runs")
    args = parser.parse_args(argv)
    warnings.filterwarnings("ignore", message=".*langchain.mcp.*")
    try:
        settings = load_settings(experiment=args.config)
        if settings.graph not in BUILT_GRAPHS:
            raise ConfigError(f"graph {settings.graph} is not built yet (C0: Phase 3, C2: Phase 4)")
        cases = load_split(args.split)
        require_live_tracing(settings)
        deps = live_deps(settings)
    except (ConfigError, ValueError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    exp = settings.experiment.id if settings.experiment else args.config.stem
    exp_id = f"{exp}-{args.split}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir = args.out / exp_id
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = asyncio.run(run_all(cases, deps, settings, out_dir, args.repeats))
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")
    from eval.report import render

    print(f"\nresults: {out_dir.relative_to(REPO_ROOT)}/results.jsonl\n")
    print(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
