"""Call guard: one FastMCP middleware in front of every tool of every server.

Order: agent token (FORBIDDEN) -> tool on the agent's allow-list (FORBIDDEN) -> case_id
present (INVALID_INPUT) and equal to the token's case (OUT_OF_SCOPE) -> the tool. Argument
validation failures become INVALID_INPUT and unexpected exceptions become INTERNAL, logged
with a correlation_id. Every ToolError result is marked isError so clients and agents see a
failed call, and carries the error in structuredContent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import mcp.types as mt
from fastmcp.exceptions import ValidationError as ArgumentError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.types import TextContent
from pydantic import ValidationError

from auditguard.schemas import ToolError
from auditguard.security.tokens import TokenError, verify_token
from mcp_servers.common.errors import new_correlation_id, tool_error

log = logging.getLogger("auditguard.mcp")

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


def error_result(err: ToolError) -> ToolResult:
    return ToolResult(
        content=[TextContent(type="text", text=err.model_dump_json())],
        structured_content={"result": err.model_dump(mode="json")},
        is_error=True,
    )


def _summarise(exc: BaseException) -> str:
    """Field paths and error types only: argument values may be untrusted text."""
    cause = exc.__cause__ if isinstance(exc.__cause__, ValidationError) else exc
    if isinstance(cause, ValidationError):
        parts = [
            f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['type']}"
            for e in cause.errors()
        ]
        return "invalid arguments: " + "; ".join(parts)
    return "invalid arguments"


def _is_tool_error(result: ToolResult) -> bool:
    sc = result.structured_content
    inner = sc.get("result") if isinstance(sc, dict) else None
    return isinstance(inner, dict) and "error_code" in inner and "correlation_id" in inner


class ToolGuard(Middleware):
    def __init__(
        self,
        server_name: str,
        *,
        token_key: bytes,
        allow_list: Mapping[str, list[str]],
        clock: Clock = utcnow,
    ) -> None:
        self.server_name = server_name
        self._key = token_key
        self._allow = {agent: frozenset(tools) for agent, tools in allow_list.items()}
        self._clock = clock

    def _check(self, tool: str, args: Mapping[str, Any], cid: str) -> ToolError | None:
        token = args.get("agent_token")
        if not isinstance(token, str) or not token:
            return tool_error("FORBIDDEN", "missing agent_token", tool, correlation_id=cid)
        try:
            claims = verify_token(token, key=self._key, now=self._clock())
        except TokenError as exc:
            return tool_error("FORBIDDEN", str(exc), tool, correlation_id=cid)
        if tool not in self._allow.get(claims.agent, frozenset()):
            return tool_error(
                "FORBIDDEN",
                f"agent {claims.agent!r} may not call {tool}",
                tool,
                correlation_id=cid,
            )
        case_id = args.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            return tool_error("INVALID_INPUT", "missing case_id", tool, correlation_id=cid)
        if case_id != claims.case_id:
            return tool_error(
                "OUT_OF_SCOPE",
                "case_id is outside this token's case scope",
                tool,
                correlation_id=cid,
            )
        return None

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool = context.message.name
        args = context.message.arguments or {}
        cid = new_correlation_id()
        denied = self._check(tool, args, cid)
        if denied is not None:
            log.warning(
                "tool call denied",
                extra={
                    "server": self.server_name,
                    "tool": tool,
                    "code": denied.error_code,
                    "correlation_id": cid,
                },
            )
            return error_result(denied)
        try:
            result = await call_next(context)
        except ArgumentError as exc:
            return error_result(
                tool_error("INVALID_INPUT", _summarise(exc), tool, correlation_id=cid)
            )
        except Exception:
            log.exception(
                "tool failed",
                extra={"server": self.server_name, "tool": tool, "correlation_id": cid},
            )
            return error_result(
                tool_error("INTERNAL", "internal error; see server log", tool, correlation_id=cid)
            )
        if _is_tool_error(result):
            result.is_error = True
        return result


def guard_from_settings(server_name: str, settings: Any, clock: Clock = utcnow) -> ToolGuard:
    from auditguard.config import require_secret

    return ToolGuard(
        server_name,
        token_key=require_secret(settings.mcp.token_key, "MCP_TOKEN_KEY"),
        allow_list=settings.mcp.allow_list,
        clock=clock,
    )
