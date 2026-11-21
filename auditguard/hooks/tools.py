"""Tool access for one caller on one case: tokens, allow-list, budget, data wrapping, records.

This is hooks v0 (Phase 1). Every MCP call made by a code node or an agent goes through a
`ToolSession`, which:
- issues the caller's agent token and adds `agent_token` and `case_id` to every call, so the
  model never sees or chooses them (they are removed from the tool schema it is shown);
- offers an agent only the tools on `mcp.allow_list[caller]` that a server actually serves
  (the servers check the same list again);
- counts calls against `budgets.case.tool_calls` and answers BUDGET_EXCEEDED once it is spent;
- wraps every result shown to a model in a `<data source="tool">` block (rule 2);
- records every result, so code can check the model's claims against what the tools returned.

Agent tools are loaded with `langchain.mcp` (`as_langchain_tool`). Phase 2 adds the post-tool
log, cost and the full budget counter (docs/08 §5).
"""

from __future__ import annotations

import json
import os
import sys
import uuid
import warnings
from collections.abc import Callable
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
        return self.max_calls is None or self.calls_made < self.max_calls

    def _record(self, tool: str, args: dict[str, Any], result: ToolResult, by_model: bool) -> None:
        ref = "" if is_error(result) else f"EV-{len(self.records) + 1:03d}"
        self.records.append(ToolRecord(tool, dict(args), result, by_model, ref))

    async def call(self, tool: str, **args: Any) -> ToolResult:
        """Call a tool from code. Returns the structured result; never raises a tool error."""
        if tool not in self._tool_server:
            return local_error("FORBIDDEN", f"{tool} is not available to {self.caller}", tool)
        if not self._budget_left():
            self.denied += 1
            return local_error("BUDGET_EXCEEDED", "tool-call budget for this case is spent", tool)
        client = self._open[self._tool_server[tool]]
        sent = {**args, "agent_token": self._token, "case_id": self.case_id}
        reply = await client.call_tool(tool, sent, raise_on_error=False)
        sc = reply.structured_content
        inner = sc.get("result") if isinstance(sc, dict) else None
        result: ToolResult = (
            inner
            if isinstance(inner, dict)
            else local_error("INTERNAL", "tool returned no structured result", tool)
        )
        self._record(tool, args, result, by_model=False)
        return result

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
            if not self._budget_left():
                self.denied += 1
                return wrap_data(
                    local_error("BUDGET_EXCEEDED", "tool-call budget for this case is spent", name)
                )
            args = {k: v for k, v in model_args.items() if k not in HIDDEN_ARGS}
            sent = {**args, "agent_token": self._token, "case_id": self.case_id}
            call = {"type": "tool_call", "id": f"call-{uuid.uuid4().hex[:12]}", "name": name}
            message = await base.ainvoke({**call, "args": sent})
            result = _result_of(message, name)
            self._record(name, args, result, by_model=True)
            return wrap_data(result, self.records[-1].ref or None)

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
