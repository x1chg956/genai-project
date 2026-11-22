"""Command line for one case (docs/02 Phase 1 task 8): `make run-case CASE=ALR-SYN-0001`.

Runs the C1 graph with the SQLite checkpointer, prints the node trace, and answers the two
interrupts from the terminal:
- decision_gate: the analyst chooses file, no_file (reason code and rationale) or needs_info;
- approval_gate: shows the exact payload and its SHA-256, then approve (approver ID, which
  must not be the analyst), edit (the narrative opens in $EDITOR; a new version and hash
  follow, and any earlier approval is void) or reject.

Rerun with the same --thread to resume a stopped run from its last checkpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from auditguard.approval.stores import approval_store
from auditguard.config import ConfigError, Settings, load_settings, resolve_path
from auditguard.db import conninfo
from auditguard.graph.deps import GraphDeps
from auditguard.graph.single_agent import checkpointer, compile_graph
from auditguard.hooks.tools import server_clients
from auditguard.llm import get_model, get_structured_model
from auditguard.prompts import load_prompt
from auditguard.report.str_payload import SECTIONS, load_reason_codes
from auditguard.schemas import Alert, DraftReport, SingleAgentAnswer
from auditguard.tracing import flush_traces, require_live_tracing, trace_config
from mcp_servers.common.guard import utcnow

Ask = Callable[[str], str]
Edit = Callable[[str], str]


# ---- editing the narrative -------------------------------------------------------------

EDIT_HELP = (
    "# One sentence per line. Start a line with [ID, ID] to cite evidence (a material claim);\n"
    "# a line without [..] is not material. Lines starting with # are ignored.\n"
)


def draft_to_text(draft: DraftReport) -> str:
    out = [f"# AuditGuard report, editing version {draft.version}", EDIT_HELP.rstrip()]
    for name in SECTIONS:
        out.append(f"## {name}")
        for s in getattr(draft.sections, name):
            out.append(f"[{', '.join(s.evidence_ids)}] {s.text}" if s.material else s.text)
    return "\n".join(out) + "\n"


_CITED = re.compile(r"^\[([^\]]*)\]\s*(.+)$")


def text_to_draft(text: str, version: int) -> dict[str, Any]:
    sections: dict[str, list[dict[str, Any]]] = {name: [] for name in SECTIONS}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or (line.startswith("#") and not line.startswith("## ")):
            continue
        if line.startswith("## "):
            current = line[3:].strip()
            if current not in sections:
                raise ValueError(f"unknown section {current!r}; use {', '.join(SECTIONS)}")
            continue
        if current is None:
            raise ValueError("text before the first '## section' heading")
        match = _CITED.match(line)
        if match:
            ids = [i.strip() for i in match.group(1).split(",") if i.strip()]
            sections[current].append({"text": match.group(2), "evidence_ids": ids})
        else:
            sections[current].append({"text": line, "material": False})
    return {"version": version, "sections": sections}


def open_in_editor(text: str) -> str:
    editor = shlex.split(os.environ.get("EDITOR") or "vi")
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        path = Path(fh.name)
    try:
        subprocess.run([*editor, str(path)], check=True)  # noqa: S603 - the user's own editor
        return path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)


# ---- printing ----------------------------------------------------------------------------


def show_decision(view: dict[str, Any], out: Callable[[str], None]) -> None:
    pack = view["evidence_pack"]
    out(f"\n=== Decision needed: case {view['case_id']} ===")
    if view.get("notice"):
        out(f"!! {view['notice']}")
    if pack.get("data_quality"):
        dq = pack["data_quality"]
        out(f"Data quality: {dq['verdict']} (duplicates {dq['duplicates_quarantined']})")
    out("Indicators (computed by the rule engine):")
    for r in pack["indicators"]:
        flag = "FIRED" if r["fired"] else "not fired"
        out(f"  {r['rule_id']} v{r['version']}: {flag}; inputs {json.dumps(r['inputs'])}")
    if pack.get("recommendation"):
        out(f"Agent recommends: {pack['recommendation']} ({pack.get('recommendation_reason', '')})")
        out("  The decision is yours.")
    out("Hypotheses:")
    for h in pack["hypotheses"]:
        out(f"  {h['hyp_id']} [{h['kind']}, {h['status']}] {h['label']} {h['evidence_ids']}")
    if pack.get("draft"):
        out("Draft:")
        for name in SECTIONS:
            for s in pack["draft"]["sections"][name]:
                out(f"  ({name}) {s['text']} {s['evidence_ids'] or ''}")
    for e in pack.get("errors", []):
        out(f"  error: {e['tool_name']}: {e['error_code']}: {e['message']}")


def show_approval(view: dict[str, Any], out: Callable[[str], None]) -> None:
    out(f"\n=== Approval needed: case {view['case_id']}, report version {view['report_version']}")
    if view.get("notice"):
        out(f"!! {view['notice']}")
    out(json.dumps(json.loads(view["payload"]), indent=2, sort_keys=True, ensure_ascii=False))
    out(f"SHA-256 of the exact payload: {view['sha256']}")
    if view.get("diff"):
        out("Changes from the previous version:\n" + view["diff"])


# ---- answering the gates -----------------------------------------------------------------


def answer_decision(ask: Ask, analyst: str) -> dict[str, Any]:
    while True:
        choice = ask("Decision [file / no_file / needs_info]: ").strip()
        if choice in ("file", "needs_info"):
            return {"decision": choice, "decided_by": analyst}
        if choice == "no_file":
            code = ask("Reason code: ").strip()
            rationale = ask("Rationale: ").strip()
            return {
                "decision": "no_file",
                "reason_code": code,
                "rationale": rationale,
                "decided_by": analyst,
            }


def answer_approval(
    view: dict[str, Any], ask: Ask, edit: Edit, current: DraftReport | None
) -> dict[str, Any]:
    while True:
        choice = ask("Principal Officer [approve / edit / reject]: ").strip()
        if choice == "approve":
            return {"action": "approve", "approver_id": ask("Approver ID: ").strip()}
        if choice == "reject":
            return {"action": "reject", "approver_id": ask("Approver ID: ").strip()}
        if choice == "edit" and current is not None:
            try:
                edited = text_to_draft(edit(draft_to_text(current)), current.version)
                DraftReport.model_validate(edited)
            except ValueError as exc:
                print(f"edit refused: {exc}")
                continue
            return {"action": "edit", "edited_draft": edited}


# ---- running -----------------------------------------------------------------------------


async def run_case(
    *,
    deps: GraphDeps,
    alert: Alert,
    analyst: str,
    thread_id: str,
    ask: Ask = input,
    edit: Edit = open_in_editor,
    out: Callable[[str], None] = print,
    traced: bool = True,
) -> dict[str, Any]:
    settings = deps.settings
    async with checkpointer(settings) as saver:
        app = compile_graph(deps, saver)
        base = {"configurable": {"thread_id": thread_id}}
        snapshot = await app.aget_state(base)  # type: ignore[arg-type]
        pending = [i for t in snapshot.tasks for i in t.interrupts]
        payload: Any
        if snapshot.values and not snapshot.next:
            out(f"thread {thread_id} already finished: {snapshot.values.get('status')}")
            return dict(snapshot.values)
        if pending:
            out(f"resuming thread {thread_id} at {snapshot.next[0]}")
            payload = Command(
                resume=_answer(pending[0].value, snapshot.values, ask, edit, out, analyst)
            )
        elif snapshot.values:
            out(f"resuming thread {thread_id} at {snapshot.next[0]}")
            payload = None
        else:
            out(f"starting case {alert.alert_id} as thread {thread_id}")
            payload = {
                "case_id": alert.alert_id,
                "run_id": thread_id,
                "alert": alert,
                "requester_id": analyst,
                "status": "NEW",
                "errors": [],
            }
        while True:
            config: dict[str, Any] = dict(base)
            if traced:
                trace_id = uuid.uuid4()
                config.update(
                    trace_config(
                        settings,
                        run_id=trace_id,
                        run_name="run-case",
                        metadata={"case_id": alert.alert_id, "run_id": thread_id},
                    )
                )
                out(f"trace_id={trace_id}")
            interrupt_value: dict[str, Any] | None = None
            stream = app.astream(payload, cast(RunnableConfig, config), stream_mode="updates")
            async for chunk in stream:
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        interrupt_value = dict(update[0].value)
                        continue
                    _trace(node, update or {}, out)
            if traced:
                flush_traces()
            state = (await app.aget_state(base)).values  # type: ignore[arg-type]
            if interrupt_value is None:
                out(f"\nfinished: {state.get('status')}")
                if state.get("receipt") is not None:
                    out(f"receipt: {state['receipt'].ack_id} for {state['receipt'].payload_sha256}")
                return dict(state)
            payload = Command(resume=_answer(interrupt_value, state, ask, edit, out, analyst))


def _trace(node: str, update: dict[str, Any], out: Callable[[str], None]) -> None:
    line = f"[{node}]"
    if "status" in update:
        line += f" status={update['status']}"
    out(line)
    for call in (update.get("agent_stats") or {}).get("tool_log", []):
        out(f"    tool {call}")
    stats = update.get("agent_stats")
    if stats:
        out(
            f"    tool_calls={stats['tool_calls']} invalid={stats['invalid_tool_calls']} "
            f"retries={stats['structured_retries']} tokens={stats['input_tokens']}+"
            f"{stats['output_tokens']} stopped_by={stats['stopped_by']}"
        )
    for err in update.get("errors") or []:
        out(f"    error {err.tool_name}: {err.error_code}: {err.message}")


def _answer(
    view: dict[str, Any],
    state: dict[str, Any],
    ask: Ask,
    edit: Edit,
    out: Callable[[str], None],
    analyst: str,
) -> dict[str, Any]:
    if view["kind"] == "decision":
        show_decision(view, out)
        return answer_decision(ask, analyst)
    show_approval(view, out)
    drafts = state.get("drafts") or []
    return answer_approval(view, ask, edit, drafts[-1] if drafts else None)


# ---- wiring ------------------------------------------------------------------------------


def load_alert(settings: Settings, alert_id: str) -> Alert:
    with psycopg.connect(conninfo(settings.databases.app, application_name="run-case")) as conn:
        row = conn.execute(
            "SELECT alert_id, account_token, scenario_code, detected_at, profile "
            "FROM ledger.alert WHERE alert_id = %s",
            (alert_id,),
        ).fetchone()
    if row is None:
        raise ConfigError(f"no alert {alert_id} in the ledger; run make seed first")
    return Alert(
        alert_id=row[0],
        account_ref=row[1],
        scenario_code=row[2],
        detected_at=row[3],
        profile=row[4],
    )


def live_deps(settings: Settings) -> GraphDeps:
    corpus = resolve_path(settings.corpus.passages)
    if not corpus.is_file():
        raise ConfigError(
            f"regulatory_kb needs {settings.corpus.passages} (make ingest). For a development "
            "run on synthetic passages: CORPUS_PASSAGES=tests/fixtures/passages_test.jsonl"
        )
    prompt = load_prompt("single_agent", settings)
    return GraphDeps(
        settings=settings,
        clients=server_clients(settings),
        approvals=approval_store(settings),
        reason_codes=load_reason_codes(resolve_path(settings.str_report.reason_codes)),
        chat_model=get_model(prompt.model_role, settings),
        answer_model=get_structured_model(prompt.model_role, SingleAgentAnswer, settings),
        prompt=prompt,
        clock=utcnow,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m auditguard.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    rc = sub.add_parser("run-case", help="run one case through the C1 graph")
    rc.add_argument("--case", required=True)
    rc.add_argument("--thread", help="resume this thread (default: a new run ID)")
    rc.add_argument("--analyst", default=os.environ.get("ANALYST_ID", "ANL-SYN-0001"))
    args = parser.parse_args(argv)
    warnings.filterwarnings("ignore", message=".*langchain.mcp.*")
    try:
        settings = load_settings()
        require_live_tracing(settings)
        deps = live_deps(settings)
        alert = load_alert(settings, args.case)
    except (ConfigError, psycopg.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    thread = args.thread or f"RUN-{uuid.uuid4().hex[:12]}"
    try:
        state = asyncio.run(
            run_case(deps=deps, alert=alert, analyst=args.analyst, thread_id=thread)
        )
    except (KeyboardInterrupt, EOFError):
        print(f"\nstopped. Resume with: make run-case CASE={args.case} THREAD={thread}")
        return 130
    return (
        0 if state.get("status") in ("SUBMITTED", "CLOSED_NO_FILE", "NEEDS_INFO", "REJECTED") else 1
    )


if __name__ == "__main__":
    sys.exit(main())
