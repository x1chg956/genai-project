"""C1 graph (docs/03 §2): ingest_validate → single_agent → decision_gate → build_payload →
approval_gate → submit → END, with close_no_file, needs_info and reject as the other ends.

Checkpoints go to Postgres (`databases.app`, schema `checkpointer.schema`) from Phase 2, or to
SQLite (`checkpointer.path`) when `checkpointer.backend` is sqlite; thread_id = run_id.
Output guardrails (Phase 2) and memory_update (Phase 5) slot in without changing these edges.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite
import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from psycopg import sql
from psycopg.conninfo import make_conninfo

import auditguard.schemas as schemas
from auditguard.config import Settings, resolve_path
from auditguard.db import conninfo
from auditguard.graph.deps import GraphDeps
from auditguard.graph.gates import (
    make_approval_gate,
    make_decision_gate,
    route_approval,
    route_decision,
)
from auditguard.graph.nodes.agent import make_agent_node
from auditguard.graph.nodes.build_payload import make_build_payload
from auditguard.graph.nodes.ingest_validate import make_ingest_validate
from auditguard.graph.nodes.submit import make_submit
from auditguard.graph.state import CaseState


def close_no_file(state: CaseState) -> dict[str, Any]:
    return {"status": "CLOSED_NO_FILE"}


def needs_info(state: CaseState) -> dict[str, Any]:
    return {"status": "NEEDS_INFO"}


def _after(node_ok: str) -> Any:
    def route(state: CaseState) -> str:
        return "end" if state.get("status") == "ERROR" else node_ok

    return route


def build_graph(deps: GraphDeps) -> StateGraph[CaseState]:
    g: StateGraph[CaseState] = StateGraph(CaseState)
    g.add_node("ingest_validate", make_ingest_validate(deps))
    g.add_node("single_agent", make_agent_node(deps))
    g.add_node("decision_gate", make_decision_gate(deps))
    g.add_node("close_no_file", close_no_file)
    g.add_node("needs_info", needs_info)
    g.add_node("build_payload", make_build_payload(deps))
    g.add_node("approval_gate", make_approval_gate(deps))
    g.add_node("submit", make_submit(deps))

    g.add_edge(START, "ingest_validate")
    g.add_conditional_edges(
        "ingest_validate", _after("single_agent"), {"single_agent": "single_agent", "end": END}
    )
    g.add_edge("single_agent", "decision_gate")
    g.add_conditional_edges(
        "decision_gate",
        route_decision,
        ["decision_gate", "build_payload", "close_no_file", "needs_info"],
    )
    g.add_edge("close_no_file", END)
    g.add_edge("needs_info", END)
    g.add_conditional_edges(
        "build_payload", _after("approval_gate"), {"approval_gate": "approval_gate", "end": END}
    )
    g.add_conditional_edges(
        "approval_gate",
        route_approval,
        {
            "approval_gate": "approval_gate",
            "build_payload": "build_payload",
            "submit": "submit",
            "end": END,
        },
    )
    g.add_edge("submit", END)
    return g


def serializer() -> JsonPlusSerializer:
    """Checkpoint serializer that allows exactly our schema classes back out of storage."""
    allowed = [
        (obj.__module__, obj.__name__)
        for obj in vars(schemas).values()
        if inspect.isclass(obj) and obj.__module__.startswith("auditguard.schemas")
    ]
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)


@asynccontextmanager
async def sqlite_checkpointer(settings: Settings) -> AsyncIterator[AsyncSqliteSaver]:
    path = resolve_path(settings.checkpointer.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(path)) as conn:
        yield AsyncSqliteSaver(conn, serde=serializer())


@asynccontextmanager
async def postgres_checkpointer(settings: Settings) -> AsyncIterator[AsyncPostgresSaver]:
    """Checkpoints in their own schema of the app database, created on first use."""
    schema = settings.checkpointer.schema_
    base = conninfo(settings.databases.app, application_name="checkpointer")
    async with await psycopg.AsyncConnection.connect(base, autocommit=True) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
    scoped = make_conninfo(base, options=f"-c search_path={schema}")
    async with AsyncPostgresSaver.from_conn_string(scoped, serde=serializer()) as saver:
        await saver.setup()
        yield saver


@asynccontextmanager
async def checkpointer(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    """The configured checkpointer (`checkpointer.backend`)."""
    if settings.checkpointer.backend == "postgres":
        async with postgres_checkpointer(settings) as pg:
            yield pg
    else:
        async with sqlite_checkpointer(settings) as lite:
            yield lite


def compile_graph(
    deps: GraphDeps, checkpointer: Any, *, interrupt_before: list[str] | None = None
) -> CompiledStateGraph[CaseState, None, CaseState, CaseState]:
    return build_graph(deps).compile(checkpointer=checkpointer, interrupt_before=interrupt_before)
