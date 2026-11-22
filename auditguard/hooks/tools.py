"""Tool access for one caller on one case: tokens, allow-list, budget, data wrapping, records.

Hooks v1 (Phase 2, docs/08 §5). Every MCP call made by a code node or an agent goes
through a `ToolSession`, which:
- issues the caller's agent token and adds `agent_token` and `case_id` to every call, so the
  model never sees or chooses them (they are removed from the tool schema it is shown);
- pre_tool: offers only the tools on `mcp.allow_list[caller]` that a server serves (the servers
  check the same list again), checks the arguments against the tool's input schema, and
  checks the case budget (tool calls, steps, tokens, seconds, cost). A denial is a ToolError
  (FORBIDDEN, INVALID_INPUT or BUDGET_EXCEEDED), never an exception;
- retries read tools only, only on retryable errors, with exponential backoff, at most twice;
- post_tool: logs one audit event per attempt (args hash, latency, result size, cost, error
  code, no argument values), updates the Prometheus counters and appends to audit_log;
- wraps every result shown to a model in a `<data source="tool">` block (rule 2);
- records every result, so code can check the model's claims against what the tools returned.

Agent tools are loaded with `langchain.mcp` (`as_langchain_tool`).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
import warnings
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from types import TracebackType
from typing import Any, Self

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool

from auditguard.config import REPO_ROOT, Settings, require_secret
from auditguard.hooks.budgets import Budget
from auditguard.hooks.post_tool import AuditSink, NullAuditSink, make_event, record_metrics
from auditguard.metrics import TOOL_RETRIES
from auditguard.security.tokens import issue_token
from mcp_servers.common.errors import new_correlation_id
from mcp_servers.common.guard import Clock, utcnow

with warnings.catch_warnings():  # langchain.mcp warns once that it is in beta
    warnings.simplefilter("ignore")
    from langchain.mcp import as_langchain_tool

HIDDEN_ARGS = ("agent_token", "case_id")
"""Filled in by code on every call; never shown to or chosen by a model."""

ClientFactory = Callable[[str], Client[Any]]
"""Server name -> a new, unconnected FastMCP client for it."""

ToolResult = dict[str, Any]
"""A tool's structured result: the output model as JSON, or a ToolError (has `error_code`)."""


def is_error(result: ToolResult) -> bool:
    return "error_code" in result


def local_error(code: str, message: str, tool: str) -> ToolResult:
    """A ToolError raised on our side of the wire (budget, unknown tool, bad reply)."""
    return {
        "error_code": code,
        "message": message,
        "retryable": False,
        "tool_name": tool,
        "correlation_id": new_correlation_id(),
    }


def server_clients(settings: Settings) -> ClientFactory:
    """Clients for the configured transport: stdio subprocesses in development, HTTP in Docker."""

    def make(server: str) -> Client[Any]:
        cfg = settings.mcp.servers[server]
        if settings.mcp.transport == "http":
            return Client(f"http://{settings.mcp.http_host}:{cfg.port}/mcp")
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", cfg.module],
            env=dict(os.environ),
            cwd=str(REPO_ROOT),
        )
        return Client(transport)

    return make


def wrap_data(result: ToolResult, ref: str | None = None) -> str:
    """Tool output as shown to a model: data, never instructions (rule 2).

    `ref` is the result's evidence ID (EV-001, ...), so a fact from an aggregate result
    such as an account summary can be cited; error results get none.
    """
    shown = {"evidence_id": ref, **result} if ref else result
    body = json.dumps(shown, ensure_ascii=False, sort_keys=True)
    return f'<data source="tool">{body}</data>'


@dataclass(frozen=True)
class ToolRecord:
    tool: str
    args: dict[str, Any]
    """Arguments as sent, without agent_token and case_id."""
    result: ToolResult
    by_model: bool
    ref: str = ""
    """Evidence ID of this result (EV-001, ...); empty for an error result."""


@dataclass
class ToolSession:
    """One caller's tool access for one case. Use as `async with ToolSession(...) as s:`."""

    caller: str
    case_id: str
    settings: Settings
    clients: ClientFactory
    servers: tuple[str, ...]
    max_calls: int | None = None
    clock: Clock = utcnow
    budget: Budget | None = None
    audit: AuditSink = field(default_factory=NullAuditSink)
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    shown: Callable[[str], str] = str
    """Applied to every result text a model sees (the pseudonymiser)."""
    records: list[ToolRecord] = field(default_factory=list)
    denied: int = 0
    _open: dict[str, Client[Any]] = field(default_factory=dict)
    _tool_server: dict[str, str] = field(default_factory=dict)
    _mcp_tools: dict[str, Any] = field(default_factory=dict)
    _stack: AsyncExitStack | None = None
    _token: str = ""

    async def __aenter__(self) -> Self:
        key = require_secret(self.settings.mcp.token_key, "MCP_TOKEN_KEY")
        ttl = timedelta(minutes=self.settings.mcp.token_ttl_minutes)
        self._token = issue_token(self.caller, self.case_id, key=key, ttl=ttl, now=self.clock())
        self._stack = AsyncExitStack()
        allowed = set(self.settings.mcp.allow_list.get(self.caller, []))
        for server in self.servers:
            client = await self._stack.enter_async_context(self.clients(server))
            self._open[server] = client
            for tool in await client.list_tools():
                if tool.name in allowed:
                    self._tool_server[tool.name] = server
                    self._mcp_tools[tool.name] = tool
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._open.clear()

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._tool_server)

    @property
    def calls_made(self) -> int:
        return len(self.records)

    def _budget_left(self) -> bool:
        if self.max_calls is not None and self.calls_made >= self.max_calls:
            return False
        return self.budget is None or self.budget.spent() is None

    def _record(self, tool: str, args: dict[str, Any], result: ToolResult, by_model: bool) -> None:
        ref = "" if is_error(result) else f"EV-{len(self.records) + 1:03d}"
        self.records.append(ToolRecord(tool, dict(args), result, by_model, ref))

    def _is_read(self, tool: str) -> bool:
        meta = getattr(self._mcp_tools.get(tool), "meta", None) or {}
        return "read" in ((meta.get("fastmcp") or {}).get("tags") or [])

    def _schema_problem(self, tool: str, args: dict[str, Any]) -> str | None:
        schema = getattr(self._mcp_tools[tool], "input_schema", None) or {}
        props = {k for k in schema.get("properties", {}) if k not in HIDDEN_ARGS}
        required = {r for r in schema.get("required", []) if r not in HIDDEN_ARGS}
        unknown = sorted(set(args) - props)
        missing = sorted(required - set(args))
        if unknown or missing:
            return f"arguments do not match {tool}'s schema: unknown {unknown}, missing {missing}"
        return None

    def _pre_tool(self, tool: str, args: dict[str, Any]) -> ToolResult | None:
        """The first reason this call may not run, as a ToolError; None when it may."""
        if tool not in self._tool_server:
            return local_error("FORBIDDEN", f"{tool} is not available to {self.caller}", tool)
        problem = self._schema_problem(tool, args)
        if problem:
            return local_error("INVALID_INPUT", problem, tool)
        if not self._budget_left():
            self.denied += 1
            what = (self.budget.spent() if self.budget else None) or "tool_calls"
            return local_error("BUDGET_EXCEEDED", f"case budget spent: {what}", tool)
        return None

    def _post_tool(
        self,
        tool: str,
        args: dict[str, Any],
        result: ToolResult,
        *,
        latency_ms: int,
        attempt: int,
        by_model: bool,
        denied: bool = False,
    ) -> None:
        event = make_event(
            now=self.clock(),
            case_id=self.case_id,
            caller=self.caller,
            server=self._tool_server.get(tool, "-"),
            tool=tool,
            args=args,
            result=result,
            latency_ms=latency_ms,
            attempt=attempt,
            by_model=by_model,
            denied=denied,
        )
        record_metrics(event)
        self.audit.append(event)

    async def _guarded(
        self,
        tool: str,
        args: dict[str, Any],
        send: Callable[[], Awaitable[ToolResult]],
        *,
        by_model: bool,
    ) -> ToolResult:
        """pre_tool, the call (with read-only retries), post_tool, then the record."""
        denial = self._pre_tool(tool, args)
        if denial is not None:
            self._post_tool(
                tool, args, denial, latency_ms=0, attempt=1, by_model=by_model, denied=True
            )
            return denial
        if self.budget is not None:
            self.budget.charge_tool_call()
        retry = self.settings.audit.retry
        attempts = retry.max_attempts if self._is_read(tool) else 1
        result: ToolResult = {}
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            result = await send()
            latency_ms = int((time.monotonic() - started) * 1000)
            self._post_tool(
                tool, args, result, latency_ms=latency_ms, attempt=attempt, by_model=by_model
            )
            if not (is_error(result) and result.get("retryable") and attempt < attempts):
                break
            TOOL_RETRIES.labels(tool).inc()
            await self.sleep(retry.base_delay_s * 2 ** (attempt - 1))
        self._record(tool, args, result, by_model=by_model)
        return result

    async def call(self, tool: str, **args: Any) -> ToolResult:
        """Call a tool from code. Returns the structured result; never raises a tool error."""

        async def send() -> ToolResult:
            client = self._open[self._tool_server[tool]]
            sent = {**args, "agent_token": self._token, "case_id": self.case_id}
            reply = await client.call_tool(tool, sent, raise_on_error=False)
            sc = reply.structured_content
            inner = sc.get("result") if isinstance(sc, dict) else None
            if isinstance(inner, dict):
                return inner
            return local_error("INTERNAL", "tool returned no structured result", tool)

        return await self._guarded(tool, args, send, by_model=False)

    async def agent_tools(self) -> list[BaseTool]:
        """The caller's allowed tools for a ReAct agent, loaded with langchain.mcp and wrapped."""
        tools: list[BaseTool] = []
        for name in self.tool_names:
            base = await as_langchain_tool(
                self._mcp_tools[name], self._open[self._tool_server[name]]
            )
            tools.append(self._wrap(base))
        return tools

    def _wrap(self, base: BaseTool) -> BaseTool:
        schema = dict(base.args_schema) if isinstance(base.args_schema, dict) else {}
        props = {k: v for k, v in schema.get("properties", {}).items() if k not in HIDDEN_ARGS}
        required = [r for r in schema.get("required", []) if r not in HIDDEN_ARGS]
        shown = {**schema, "properties": props, "required": required}
        name = base.name

        async def run(**model_args: Any) -> str:
            args = {k: v for k, v in model_args.items() if k not in HIDDEN_ARGS}

            async def send() -> ToolResult:
                sent = {**args, "agent_token": self._token, "case_id": self.case_id}
                call = {"type": "tool_call", "id": f"call-{uuid.uuid4().hex[:12]}", "name": name}
                message = await base.ainvoke({**call, "args": sent})
                return _result_of(message, name)

            before = len(self.records)
            result = await self._guarded(name, args, send, by_model=True)
            ref = self.records[-1].ref if len(self.records) > before else ""
            return self.shown(wrap_data(result, ref or None))

        return StructuredTool(
            name=name,
            description=base.description,
            args_schema=shown,
            coroutine=run,
            metadata=base.metadata,
        )


def _result_of(message: Any, tool: str) -> ToolResult:
    """The structured result inside a langchain.mcp ToolMessage (success or ToolError)."""
    if not isinstance(message, ToolMessage):
        return local_error("INTERNAL", "unexpected tool reply", tool)
    artifact = message.artifact
    if isinstance(artifact, dict):
        sc = artifact.get("structured_content")
        inner = sc.get("result") if isinstance(sc, dict) else None
        if isinstance(inner, dict):
            return inner
    # Errors arrive as content: the server's ToolError JSON in a text block.
    blocks = message.content if isinstance(message.content, list) else [message.content]
    for block in blocks:
        text = block.get("text") if isinstance(block, dict) else block
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
            except ValueError:
                continue
            if isinstance(parsed, dict) and "error_code" in parsed:
                return parsed
    return local_error("INTERNAL", "tool returned no structured result", tool)
