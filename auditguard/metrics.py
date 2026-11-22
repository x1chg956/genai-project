"""Prometheus metrics (docs/11 §6). Process-wide, registered once on import."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

TOOL_CALLS = Counter(
    "tool_calls_total", "MCP tool calls through the hooks", ["server", "tool", "status"]
)
TOOL_ERRORS = Counter("tool_errors_total", "MCP tool errors by code", ["server", "code"])
TOOL_RETRIES = Counter("tool_retries_total", "Read-tool retries after a retryable error", ["tool"])
TOOL_LATENCY = Histogram("tool_latency_seconds", "MCP tool call latency", ["server", "tool"])
